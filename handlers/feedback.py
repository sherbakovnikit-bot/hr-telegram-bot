import html
import logging
from datetime import timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from models import CandidateFeedbackState, OnboardingFollowupState
from core import settings, database
from utils.helpers import (
    safe_answer_callback_query,
    send_or_edit_message,
    add_to_sheets_queue,
    build_inline_keyboard,
    get_now,
    get_user_data_from_update,
    send_new_menu_message
)
from utils.keyboards import CANDIDATE_FEEDBACK_RATING_OPTIONS, YES_NO_OPTIONS, RESTAURANT_OPTIONS
from handlers.common import cancel, prompt_to_use_button
from handlers.onboarding import start_onboarding_flow

logger = logging.getLogger(__name__)

async def schedule_candidate_feedback(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    candidate_id = job_data["candidate_id"]

    text = "Привет еще раз! 👋 Прошло немного времени после твоего визита. Будем очень благодарны, если ты ответишь на 4 коротких вопроса о встрече. Это поможет нам стать лучше."
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✍️ Пройти опрос (1 минута)",
                               callback_data=settings.CALLBACK_START_CANDIDATE_FEEDBACK)]])

    try:
        await context.bot.send_message(candidate_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to send candidate feedback poll to {candidate_id}: {e}")


async def schedule_onboarding_noshow_check(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    candidate_id = job_data.get("candidate_id")

    if not candidate_id:
        logger.error("No candidate_id in schedule_onboarding_noshow_check job.")
        return

    if await database.is_survey_completed("onboarding", candidate_id):
        logger.info(f"Candidate {candidate_id} has completed onboarding survey. No-show check cancelled.")
        return

    logger.info(f"Starting onboarding NO-SHOW check for candidate {candidate_id}.")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Да, все в силе / смена перенеслась", callback_data=settings.CALLBACK_ONBOARDING_FOLLOWUP_YES)],
        [InlineKeyboardButton("Нет, я передумал(а) / не могу выйти", callback_data=settings.CALLBACK_ONBOARDING_FOLLOWUP_NO)],
    ])
    text = ("Привет! 👋 На связи команда «Марчеллис».\n\n"
            "Заметили, что ты не прошел(-ла) опрос после ознакомительной смены. "
            "Хотели бы уточнить твой статус: твои планы по работе у нас еще актуальны?")

    try:
        sent_message = await context.bot.send_message(candidate_id, text, reply_markup=keyboard)
        context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = sent_message.message_id
        context.user_data['candidate_id_for_noshow'] = candidate_id
    except Exception as e:
        logger.error(f"Failed to send onboarding no-show check to {candidate_id}: {e}")


async def start_onboarding_followup(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> OnboardingFollowupState | int:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if query.data == settings.CALLBACK_ONBOARDING_FOLLOWUP_YES:
        await send_or_edit_message(update, context,
                                   "Отлично! Рады, что ты с нами. Пожалуйста, не забудь пройти опрос после своей первой смены (ссылку тебе давал менеджер).")
        return ConversationHandler.END

    elif query.data == settings.CALLBACK_ONBOARDING_FOLLOWUP_NO:
        await send_or_edit_message(update, context,
                                   "Очень жаль это слышать. Если не сложно, напиши, пожалуйста, что повлияло на твое решение?")
        return OnboardingFollowupState.AWAITING_LEAVING_REASON

    return ConversationHandler.END


async def leaving_reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reason = update.message.text
    user_id, user_name, _ = get_user_data_from_update(update)
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")

    restaurant_code = await database.get_candidate_restaurant(user_id)
    restaurant_name = "Неизвестно"
    if restaurant_code:
         restaurant_name = next((name for name, r_code in RESTAURANT_OPTIONS if r_code.endswith(restaurant_code)), restaurant_code)

    row_data = [timestamp, user_name, user_id, restaurant_name, reason]
    await add_to_sheets_queue(settings.CANDIDATE_NOSHOW_SHEET_NAME, row_data)

    manager_ids = []
    if restaurant_code:
        manager_ids = await database.get_managers_for_restaurant(restaurant_code)

    recipients = set(settings.ADMIN_IDS).union(set(manager_ids))
    notification_tasks = await database.get_all_pending_feedback_for_candidate(user_id)

    if notification_tasks and recipients:
        message = (f"❗️🗣️ <b>Кандидат передумал выходить на смену</b>\n\n"
                   f"<b>Кандидат:</b> {html.escape(user_name)}\n"
                   f"<b>Ресторан:</b> {html.escape(restaurant_name)}\n\n"
                   f"<b>Причина, указанная кандидатом:</b>\n<pre>{html.escape(reason)}</pre>\n\n"
                   f"<i>Задачи по этому кандидату закрыты.</i>")

        for task in notification_tasks:
            if task['manager_id'] in recipients:
                try:
                    await context.bot.send_message(
                        chat_id=task['manager_id'],
                        text=message,
                        reply_to_message_id=task['message_id'],
                        parse_mode=ParseMode.HTML,
                        allow_sending_without_reply=True
                    )
                except Exception as e:
                    logger.error(f"Failed to send leaving reason to manager/admin {task['manager_id']}: {e}")

    # Вместо полного удаления данных, просто удаляем его из очереди на рассмотрение
    await database.remove_all_pending_feedback_for_candidate(user_id)
    # Статус is_active остается 0, что корректно для отказавшегося кандидата
    logger.info(f"User {user_id} changed their mind. Reason recorded, pending feedback cleared. User data retained as inactive.")

    await send_or_edit_message(update, context,
                               "Спасибо за честный ответ. Эта информация поможет нам стать лучше. Удачи в дальнейших поисках!")
    return ConversationHandler.END


onboarding_followup_conversation_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_onboarding_followup,
                             pattern=f"^{settings.CALLBACK_ONBOARDING_FOLLOWUP_YES}$|^{settings.CALLBACK_ONBOARDING_FOLLOWUP_NO}$")
    ],
    states={
        OnboardingFollowupState.AWAITING_LEAVING_REASON: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, leaving_reason_received)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    name="onboarding_followup_conv",
    persistent=True,
    per_message=False
)


async def start_candidate_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> CandidateFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data.clear()
    context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = query.message.message_id

    keyboard = build_inline_keyboard(CANDIDATE_FEEDBACK_RATING_OPTIONS, columns=5)
    text = "<b>Вопрос 1/4:</b> Как в целом прошло интервью? Пожалуйста, оцени от 1 (плохо) до 5 (отлично)."
    await send_or_edit_message(update, context, text, keyboard)
    return CandidateFeedbackState.AWAITING_IMPRESSION


async def impression_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> CandidateFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data["impression_rating"] = query.data.split("_")[-1]
    context.user_data["candidate_name"] = update.effective_user.full_name
    context.user_data["candidate_id"] = update.effective_user.id

    keyboard = build_inline_keyboard(YES_NO_OPTIONS, columns=2)
    text = "<b>Вопрос 2/4:</b> Рассказал ли тебе менеджер обо всех условиях работы (график, зарплата, обязанности)?"
    await send_or_edit_message(update, context, text, keyboard)
    return CandidateFeedbackState.AWAITING_CONDITIONS_MET


async def conditions_met_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> CandidateFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data["conditions_met"] = "Да" if query.data == "yes" else "Нет"

    keyboard = build_inline_keyboard(YES_NO_OPTIONS, columns=2)
    text = "<b>Вопрос 3/4:</b> Были ли тебе понятны требования к кандидату на эту должность?"
    await send_or_edit_message(update, context, text, keyboard)
    return CandidateFeedbackState.AWAITING_REQUIREMENTS_CLEAR


async def requirements_clear_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> CandidateFeedbackState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data["requirements_clear"] = "Да" if query.data == "yes" else "Нет"

    text = "<b>Вопрос 4/4:</b> Может быть, у тебя есть дополнительные комментарии или пожелания для нас? (Если нет, напиши «нет»)"
    await send_or_edit_message(update, context, text, None)
    return CandidateFeedbackState.AWAITING_ADDITIONAL_COMMENTS


async def additional_comments_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["additional_comments"] = update.message.text

    user_data = context.user_data
    candidate_id = user_data["candidate_id"]
    candidate_name = user_data["candidate_name"]
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")

    restaurant_code = await database.get_candidate_restaurant(candidate_id)

    impression_rating_str = user_data.get("impression_rating")
    conditions_met = user_data.get("conditions_met")
    requirements_clear = user_data.get("requirements_clear")
    additional_comments = user_data.get("additional_comments", 'Нет')

    row_data = [timestamp, candidate_name, candidate_id, impression_rating_str, conditions_met, requirements_clear,
                additional_comments]
    await add_to_sheets_queue(settings.CANDIDATE_FEEDBACK_SHEET_NAME, row_data)

    await database.log_survey_completion('candidate_feedback', candidate_id, restaurant_code)

    flags = []
    message_title = "📝 <b>ОС от кандидата после собеседования</b>"

    try:
        if impression_rating_str and int(impression_rating_str) < 3:
            flags.append(f"🚩 <b>Низкая оценка интервью: {impression_rating_str}/5</b>")
    except (ValueError, TypeError):
        pass

    if conditions_met == "Нет":
        flags.append("⚠️ <b>Условия работы были не озвучены или не понятны.</b>")
    if requirements_clear == "Нет":
        flags.append("⚠️ <b>Требования к кандидату были не ясны.</b>")

    if flags:
        message_title = "❗️🚩 <b>Критическая ОС от кандидата!</b> 🚩❗️"

    admin_message_parts = [message_title]
    admin_message_parts.append(f"\n<b>Кандидат:</b> {html.escape(candidate_name)} (ID: <code>{candidate_id}</code>)")

    if flags:
        admin_message_parts.append("\n<b>Критические моменты:</b>")
        admin_message_parts.extend(flags)
        admin_message_parts.append("\n" + "=" * 20 + "\n")

    admin_message_parts.append("<b>Полный отзыв:</b>")
    admin_message_parts.append(f"<b>Оценка интервью:</b> {impression_rating_str}/5")
    admin_message_parts.append(f"<b>Условия озвучены:</b> {conditions_met}")
    admin_message_parts.append(f"<b>Требования понятны:</b> {requirements_clear}")
    admin_message_parts.append(f"<b>Комментарий:</b> {html.escape(additional_comments)}")

    admin_message = "\n".join(admin_message_parts)

    admin_tasks = await database.get_all_pending_feedback_for_candidate(candidate_id)
    for task in admin_tasks:
        if task['manager_id'] in settings.ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=task['manager_id'],
                    text=admin_message,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=task['message_id'],
                    allow_sending_without_reply=True
                )
            except Exception as e:
                logger.error(f"Failed to send candidate feedback to admin {task['manager_id']}: {e}")

    await send_or_edit_message(update, context, "Спасибо большое за обратную связь! Она поможет нам стать лучше. 🙏")

    return ConversationHandler.END


candidate_feedback_conversation_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_candidate_feedback, pattern=f"^{settings.CALLBACK_START_CANDIDATE_FEEDBACK}$")],
    states={
        CandidateFeedbackState.AWAITING_IMPRESSION: [CallbackQueryHandler(impression_received, pattern="^cand_rate_"),
                                                     MessageHandler(filters.TEXT, prompt_to_use_button)],
        CandidateFeedbackState.AWAITING_CONDITIONS_MET: [
            CallbackQueryHandler(conditions_met_received, pattern="^(yes|no)$"),
            MessageHandler(filters.TEXT, prompt_to_use_button)],
        CandidateFeedbackState.AWAITING_REQUIREMENTS_CLEAR: [
            CallbackQueryHandler(requirements_clear_received, pattern="^(yes|no)$"),
            MessageHandler(filters.TEXT, prompt_to_use_button)],
        CandidateFeedbackState.AWAITING_ADDITIONAL_COMMENTS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, additional_comments_received)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="candidate_feedback_conv",
    persistent=True,
    per_message=False,
)