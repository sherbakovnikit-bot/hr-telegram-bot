import html
import logging
import time
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, User
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

from models import ManagerFeedbackState, MainMenuState
from core import settings, database
from utils.helpers import (
    safe_answer_callback_query,
    add_to_sheets_queue,
    get_now,
    send_or_edit_message,
    send_transient_message,
    format_user_for_sheets,
    TIMEZONE
)
from utils.keyboards import (
    MANAGER_FEEDBACK_OPTIONS,
    get_shift_date_keyboard,
    build_inline_keyboard
)
from handlers.common import cancel
from handlers.feedback import schedule_onboarding_noshow_check

logger = logging.getLogger(__name__)


async def start_manager_feedback_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    feedback_id = context.user_data.get('feedback_id')
    if not feedback_id:
        await query.answer("Произошла ошибка, не удалось найти задачу. Попробуйте снова.", show_alert=True)
        return MainMenuState.MAIN

    feedback_task = await database.get_pending_feedback_by_id(feedback_id)
    if not feedback_task:
        await query.answer("Эта задача уже неактуальна.", show_alert=True)
        return MainMenuState.MAIN

    context.user_data["job_data"] = feedback_task.get("job_data", {})
    context.user_data["candidate_id"] = feedback_task.get("candidate_id")
    candidate_name = feedback_task.get("candidate_name", "Неизвестный кандидат")

    keyboard = build_inline_keyboard(MANAGER_FEEDBACK_OPTIONS, 1)

    text = f"Оценка кандидата: <b>{html.escape(candidate_name)}</b>.\n\nПожалуйста, поделись своим решением:"
    await send_or_edit_message(update, context, text, keyboard)

    return ManagerFeedbackState.AWAITING_DECISION


async def decision_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    base_callback = query.data
    status_text = next((name for name, data in MANAGER_FEEDBACK_OPTIONS if data == base_callback), "Неизвестно")
    status_code_part = base_callback.replace(settings.CALLBACK_MGR_FEEDBACK_PREFIX, "")

    context.user_data["feedback_status_code"] = status_code_part
    context.user_data["feedback_status_text"] = status_text.strip("✅🤔❌⛔️ ")

    if status_code_part == "onboarding":
        keyboard = get_shift_date_keyboard()
        text = (f"Отлично! Статус кандидата: <b>{context.user_data['feedback_status_text']}</b>.\n\n"
                "Теперь укажи дату первого дня ознакомительной смены:")
        await send_or_edit_message(update, context, text, keyboard)
        return ManagerFeedbackState.AWAITING_SHIFT_DATE
    else:
        text = (f"Твой выбор: <b>{context.user_data['feedback_status_text']}</b>.\n\n"
                "Пожалуйста, напиши краткую причину такого решения. Это очень важно для нас.")
        await send_or_edit_message(update, context, text, None)
        return ManagerFeedbackState.AWAITING_REASON


async def shift_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if query.data == "shift_date_other":
        text = "Пожалуйста, введи дату в формате ДД.ММ.ГГГГ (например, 25.12.2024):"
        await send_or_edit_message(update, context, text, None)
        return ManagerFeedbackState.AWAITING_MANUAL_SHIFT_DATE
    else:
        date_str = query.data.replace("shift_date_", "")
        context.user_data["shift_date"] = date_str
        text = (f"Дата смены: <b>{datetime.fromisoformat(date_str).strftime('%d %B %Y')}</b>.\n\n"
                "Теперь укажи время смены (например, 10-16 или 17-23).")
        await send_or_edit_message(update, context, text, None)
        return ManagerFeedbackState.AWAITING_SHIFT_TIME


async def manual_shift_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    try:
        parsed_date = datetime.strptime(update.message.text, "%d.%m.%Y").date()
        context.user_data["shift_date"] = parsed_date.isoformat()
        text = (f"Дата смены: <b>{parsed_date.strftime('%d %B %Y')}</b>.\n\n"
                "Теперь укажи время смены (например, 10-16 или 17-23).")
        await send_or_edit_message(update, context, text)
        return ManagerFeedbackState.AWAITING_SHIFT_TIME
    except ValueError:
        await send_transient_message(context, update.effective_chat.id,
                                     "Неверный формат даты. Пожалуйста, введи в формате ДД.ММ.ГГГГ.")
        await update.message.delete()
        return ManagerFeedbackState.AWAITING_MANUAL_SHIFT_DATE


async def shift_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    context.user_data["shift_time"] = update.message.text
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Пропустить комментарий", callback_data="skip_comment")
    ]])
    text = "Отлично. Если хочешь оставить комментарий, напиши его. " \
           "Если нет, просто нажми /skip или кнопку ниже."
    await send_or_edit_message(update, context, text, keyboard)
    return ManagerFeedbackState.AWAITING_COMMENT


async def comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["feedback_comment"] = update.message.text
    await send_or_edit_message(update, context, "Спасибо, твоя обратная связь сохранена! 🙏")
    await process_manager_feedback(context, update.effective_user)
    return ConversationHandler.END


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if query:
        await safe_answer_callback_query(query)
    context.user_data["feedback_comment"] = "Не указан"
    text = "Спасибо, твоя обратная связь сохранена! 🙏"
    await send_or_edit_message(update, context, text)
    await process_manager_feedback(context, user)
    return ConversationHandler.END


async def process_manager_feedback(context: ContextTypes.DEFAULT_TYPE, responding_user: User):
    job_data = context.user_data.get("job_data", {})
    restaurant_code = job_data.get("interview_restaurant_code")
    candidate_id = job_data.get("candidate_id")
    candidate_name = job_data.get("candidate_name")
    status = context.user_data.get("feedback_status_text")
    status_code = context.user_data.get("feedback_status_code")
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")

    if not candidate_id:
        logger.error(f"CRITICAL: No candidate_id found in user_data during feedback processing for user {responding_user.id}")
        return

    await database.log_survey_completion('manager_feedback', responding_user.id, restaurant_code)
    all_tasks_for_candidate = await database.get_all_pending_feedback_for_candidate(candidate_id)
    manager_details = await database.get_manager_details(responding_user.id)

    manager_link = format_user_for_sheets(
        responding_user.id,
        manager_details.get('full_name') if manager_details else responding_user.full_name,
        manager_details.get('username') if manager_details else responding_user.username
    )

    reply_text = ""
    is_final_decision = status_code in ["onboarding", "refused", "unsuitable"]

    if status_code == "onboarding":
        shift_date_iso = context.user_data.get("shift_date", "Не указана")
        shift_date_str = "Не указана"
        if shift_date_iso != "Не указана":
            try:
                shift_date_dt = datetime.fromisoformat(shift_date_iso)
                shift_date_str = shift_date_dt.strftime('%d %B %Y')

                if context.job_queue:
                    check_time_naive = datetime.combine(shift_date_dt.date(), datetime.max.time())
                    check_time_aware = check_time_naive.replace(tzinfo=TIMEZONE) + timedelta(days=1)
                    job_context = {"candidate_id": candidate_id}
                    context.job_queue.run_once(
                        schedule_onboarding_noshow_check,
                        when=check_time_aware,
                        data=job_context,
                        name=f"onboarding_noshow_{candidate_id}"
                    )
                    logger.info(f"Scheduled onboarding no-show check for {candidate_id} on {check_time_aware}")
            except (ValueError, TypeError):
                shift_date_str = shift_date_iso

        shift_time = context.user_data.get("shift_time", "Не указано")
        comment = context.user_data.get("feedback_comment", "Не указан")
        reason = "Принят на смену"

        row_data = [timestamp, candidate_name, candidate_id, status, reason, manager_link, shift_date_iso, shift_time, comment]
        await add_to_sheets_queue(settings.MANAGER_FEEDBACK_SHEET_NAME, row_data)

        reply_text_parts = [
            f"✅ <b>Статус кандидата обновлен:</b> {html.escape(status)}",
            f"🗓 <b>Дата смены:</b> {html.escape(shift_date_str)}",
            f"⏰ <b>Время:</b> {html.escape(shift_time)}",
        ]
        if comment and comment != "Не указан":
            reply_text_parts.append(f"💬 <b>Комментарий:</b> {html.escape(comment)}")
        reply_text_parts.append(f"<i>Решение принял(а): {responding_user.mention_html()}</i>")
        reply_text = "\n".join(reply_text_parts)

        context.bot_data.setdefault('candidate_check_info', {})[candidate_id] = {
            "position": job_data.get('position', '—'),
            "full_name": job_data.get('full_name', '—'),
            "address": job_data.get('address', '—'),
            "phone": job_data.get('phone', '—'),
            "timestamp": time.time()
        }
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Проверка кандидата", callback_data=f"check_candidate_{candidate_id}")]
        ])
        await context.bot.send_message(chat_id=responding_user.id, text="Кандидат одобрен. Нажмите кнопку ниже для получения данных для проверки.", reply_markup=keyboard)
    else:
        reason = context.user_data.get("feedback_reason", "Не указана")
        row_data = [timestamp, candidate_name, candidate_id, status, reason, manager_link, "", "", ""]
        await add_to_sheets_queue(settings.MANAGER_FEEDBACK_SHEET_NAME, row_data)

        if status_code in ["refused", "unsuitable"]:
            logger.info(f"Candidate {candidate_id} was refused/unsuitable. Data will be deleted.")
            await database.delete_user_data(candidate_id)

        reply_text = (
            f"❗️ <b>Статус кандидата обновлен:</b> {html.escape(status)}\n"
            f"<b>Причина:</b> {html.escape(reason)}\n"
            f"<i>Решение принял(а): {responding_user.mention_html()}</i>"
        )

    if all_tasks_for_candidate:
        if is_final_decision:
            await database.move_pending_feedback_to_history(candidate_id, responding_user.id, status)
            logger.info(f"Final decision made for candidate {candidate_id}. All pending tasks moved to history.")
        else:
            logger.info(f"Intermediate status '{status}' for candidate {candidate_id}. Tasks remain for now.")

        for task in all_tasks_for_candidate:
            try:
                await context.bot.send_message(
                    chat_id=task['manager_id'],
                    text=reply_text,
                    reply_to_message_id=task['message_id'],
                    parse_mode=ParseMode.HTML,
                    allow_sending_without_reply=True
                )
                if is_final_decision and task['manager_id'] != responding_user.id:
                    original_message = await context.bot.edit_message_reply_markup(
                        chat_id=task['manager_id'],
                        message_id=task['message_id'],
                        reply_markup=None
                    )
                    await context.bot.edit_message_text(
                         text=f"{original_message.text}\n\n<i>(Обработано: {responding_user.mention_html()})</i>",
                         chat_id=task['manager_id'],
                         message_id=task['message_id'],
                         parse_mode=ParseMode.HTML
                    )
            except BadRequest as e:
                if "message to reply not found" not in str(e).lower():
                    logger.warning(f"Could not send feedback update to manager {task['manager_id']} (BadRequest): {e}")
            except Exception as e:
                logger.error(f"Failed to send feedback reply to manager {task['manager_id']}: {e}")