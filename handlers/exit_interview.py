import html
import time
import logging
import asyncio
from datetime import timedelta

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import Forbidden, BadRequest

from models import ExitState
from core import settings, database, stickers
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_or_edit_message,
    add_to_sheets_queue,
    get_now,
    cleanup_chat
)
from utils.keyboards import (
    RESTAURANT_OPTIONS,
    EXIT_POSITION_OPTIONS,
    DURATION_OPTIONS,
    RATING_OPTIONS,
    TRAINING_OPTIONS,
    FEEDBACK_OPTIONS,
    build_inline_keyboard
)
from handlers.common import cancel

logger = logging.getLogger(__name__)


async def schedule_exit_interview_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    first_check = job_data.get("first_check", True)

    if await database.is_survey_completed("exit", user_id):
        logger.info(f"User {user_id} has already completed the exit interview. Reminder job cancelled.")
        return

    if first_check:
        logger.info(f"User {user_id} did not complete exit interview after 3 days. Sending reminder.")
        try:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("👋 Да, готов(а) помочь", callback_data=settings.CALLBACK_START_EXIT)]])
            await context.bot.send_message(
                user_id,
                "Привет! Напоминаем о нашей просьбе пройти небольшой опрос после увольнения. Твой опыт очень важен для нас! 🙏",
                reply_markup=keyboard
            )
            context.job_queue.run_once(
                schedule_exit_interview_reminder,
                when=timedelta(days=3),
                data={"user_id": user_id, "first_check": False},
                name=f"exit_delete_check_{user_id}"
            )
        except (Forbidden, BadRequest):
            logger.warning(f"Could not send exit interview reminder to {user_id}. Deleting user data now.")
            await database.delete_user_data(user_id)
    else:
        logger.warning(f"User {user_id} did not complete exit interview after another 3 days. Deleting user data.")
        await database.delete_user_data(user_id)


async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.chat_member or not update.chat_member.new_chat_member or not update.chat_member.new_chat_member.user:
        return

    member_update = update.chat_member
    target_user = member_update.new_chat_member.user

    if not target_user or target_user.id == context.bot.id:
        return

    new_status = member_update.new_chat_member.status
    if new_status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return

    target_user_id = target_user.id
    target_user_name = target_user.full_name

    logger.info(f"User {target_user_name} ({target_user_id}) left or was banned from chat {member_update.chat.title}.")

    users_interacted = context.bot_data.get("users_interacted", set())
    if target_user_id not in users_interacted:
        logger.info(f"User {target_user_id} has not interacted with the bot before. Skipping exit interview invite.")
        return

    if context.job_queue.get_jobs_by_name(f"exit_reminder_{target_user_id}"):
        logger.info(f"Exit interview flow is already active for user {target_user_id}. Ignoring new event.")
        return

    try:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, это связано с увольнением", callback_data=settings.CALLBACK_CONFIRM_QUIT)],
            [InlineKeyboardButton("Нет, просто вышел(а) из чата", callback_data=settings.CALLBACK_DECLINE_QUIT)],
        ])
        sent_message = await context.bot.send_message(
            chat_id=target_user_id,
            text="Привет! 👋 Заметили, что ты покинул(а) наш рабочий чат.\n\n"
                 "Если это связано с увольнением, мы будем очень благодарны за обратную связь. Если нет — просто нажми вторую кнопку.",
            reply_markup=keyboard
        )
        context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = sent_message.message_id
        logger.info(f"Sent exit clarification to user {target_user_name} (ID: {target_user_id}).")

        context.job_queue.run_once(
            schedule_exit_interview_reminder,
            when=timedelta(days=3),
            data={"user_id": target_user_id, "first_check": True},
            name=f"exit_reminder_{target_user_id}"
        )

    except (Forbidden, BadRequest):
        logger.warning(f"Failed to send exit clarification to user {target_user_id} (Forbidden/BadRequest).")
    except Exception as e:
        logger.error(f"Error sending exit clarification to user {target_user_id}: {e}", exc_info=True)


async def remove_exit_jobs(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    jobs_reminder = context.job_queue.get_jobs_by_name(f"exit_reminder_{user_id}")
    jobs_delete = context.job_queue.get_jobs_by_name(f"exit_delete_check_{user_id}")
    for job in jobs_reminder + jobs_delete:
        job.schedule_removal()
    logger.info(f"Removed exit interview reminder/deletion jobs for user {user_id}.")


async def handle_quit_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback_query(query)
    if not query.message:
        return

    user_id = query.from_user.id

    if query.data == settings.CALLBACK_CONFIRM_QUIT:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("👋 Да, готов(а) помочь", callback_data=settings.CALLBACK_START_EXIT)]])
        text = ("Нам будет тебя не хватать... 🙏\n\n"
                "Любое прощание — это немного грустно, но мы уважаем твой выбор и желаем огромной удачи на новом пути! "
                "Спасибо за всё, что ты сделал(а) для команды «Марчеллис». Твой опыт очень ценен для нас.\n\n"
                "Если не сложно, удели, пожалуйста, <b>пару минут</b> и ответь на 9 анонимных вопросов. Это поможет нам стать лучше для будущих коллег ✨\n\n"
                "Готов(а) начать?")
        await send_or_edit_message(update, context, text, keyboard)
        context.user_data['chat_id'] = query.message.chat_id

    elif query.data == settings.CALLBACK_DECLINE_QUIT:
        await remove_exit_jobs(user_id, context)
        await send_or_edit_message(update, context, "Понятно! Спасибо за уточнение. Хорошего дня! 😊")


async def start_exit_interview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data.clear()
    context.user_data['_in_exit_interview'] = True
    context.user_data['chat_id'] = query.message.chat_id
    context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = query.message.message_id


    await send_or_edit_message(update, context, "Спасибо тебе за готовность помочь! 🙏 Давай начнем.", None)

    await asyncio.sleep(1)

    keyboard = build_inline_keyboard(RESTAURANT_OPTIONS, columns=2)
    text = "<b>Вопрос 1/9</b>\nВ каком <b>ресторане</b> ты работал(а) в последнее время?"
    await send_or_edit_message(update, context, text, keyboard)

    return ExitState.RESTAURANT


async def exit_restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    restaurant = next((b[0] for b in RESTAURANT_OPTIONS if b[1] == query.data), "N/A")
    restaurant_code = query.data.replace("res_", "")
    context.user_data["exit_restaurant"] = restaurant
    context.user_data["exit_restaurant_code"] = restaurant_code
    keyboard = build_inline_keyboard(EXIT_POSITION_OPTIONS, columns=2)
    text = f"Ресторан: <b>{html.escape(restaurant)}</b>.\n\n<b>Вопрос 2/9</b>\nНа какой <b>должности</b> ты работал(а)?"
    await send_or_edit_message(update, context, text, keyboard)
    return ExitState.POSITION


async def exit_position_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    position = next((b[0] for b in EXIT_POSITION_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["exit_position"] = position
    keyboard = build_inline_keyboard(DURATION_OPTIONS, columns=3)
    text = f"Должность: <b>{html.escape(position)}</b>.\n\n<b>Вопрос 3/9</b>\nКак <b>долго</b> ты проработал(а) в нашей компании?"
    await send_or_edit_message(update, context, text, keyboard)
    return ExitState.DURATION


async def exit_duration_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    duration = next((b[0] for b in DURATION_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["exit_duration"] = duration
    text = f"Стаж: <b>{html.escape(duration)}</b>.\n\n<b>Вопрос 4/9</b>\nЧто стало <b>основной причиной</b> твоего решения уйти? (Будем благодарны за честный ответ)"
    await send_or_edit_message(update, context, text)
    return ExitState.AWAITING_REASON


async def exit_reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    context.user_data["exit_reason"] = update.message.text.strip()
    text = "Спасибо!\n\n" \
           "<b>Вопрос 5/9</b>\nЧто, по-твоему, можно было бы <b>улучшить</b> в нашей работе или процессах?"
    await send_or_edit_message(update, context, text)
    return ExitState.AWAITING_IMPROVEMENT


async def exit_improvement_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    context.user_data["exit_improvement"] = update.message.text.strip()
    keyboard = build_inline_keyboard(RATING_OPTIONS, columns=3)
    text = "Отличные идеи, спасибо!\n\n" \
           "<b>Вопрос 6/9</b>\nКак бы ты оценил(а) взаимоотношения и <b>поддержку</b> со стороны твоего <b>непосредственного руководителя</b>?"
    await send_or_edit_message(update, context, text, keyboard)
    return ExitState.LEADERSHIP


async def exit_leadership_rated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    rating = next((b[0] for b in RATING_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["exit_leadership_rating"] = rating
    keyboard = build_inline_keyboard(TRAINING_OPTIONS, columns=2)
    text = f"Оценка руководителю: <b>{html.escape(rating)}</b>.\n\n<b>Вопрос 7/9</b>\nНасколько <b>достаточным</b> было <b>обучение</b>, которое ты получил(а) для работы?"
    await send_or_edit_message(update, context, text, keyboard)
    return ExitState.TRAINING


async def exit_training_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    training = next((b[0] for b in TRAINING_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["exit_training_rating"] = training
    keyboard = build_inline_keyboard(FEEDBACK_OPTIONS, columns=2)
    text = f"Оценка обучения: <b>{html.escape(training)}</b>.\n\n<b>Вопрос 8/9</b>\nКак часто ты получал(а) <b>обратную связь</b> о своей работе?"
    await send_or_edit_message(update, context, text, keyboard)
    return ExitState.FEEDBACK


async def exit_feedback_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ExitState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    feedback = next((b[0] for b in FEEDBACK_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["exit_feedback_freq"] = feedback
    text = f"Частота обратной связи: <b>{html.escape(feedback)}</b>. Остался последний шаг! 🏁\n\n<b>Вопрос 9/9</b>\nЕсть ли <b>что-то еще</b>, что ты хотел(а) бы добавить или пожелать бывшим коллегам?"
    await send_or_edit_message(update, context, text)
    return ExitState.AWAITING_COMMENTS


async def exit_comments_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["exit_comments"] = update.message.text.strip()
    user_id, user_name, _ = get_user_data_from_update(update)
    data = context.user_data
    restaurant_code = data.get("exit_restaurant_code")
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = [
        timestamp, user_name, data.get("exit_restaurant", "N/A"), data.get("exit_position", "N/A"),
        data.get("exit_duration", "N/A"), data.get("exit_reason", "N/A"), data.get("exit_improvement", "N/A"),
        data.get("exit_leadership_rating", "N/A"), data.get("exit_training_rating", "N/A"),
        data.get("exit_feedback_freq", "N/A"), data.get("exit_comments", "N/A")
    ]

    await add_to_sheets_queue(settings.EXIT_INTERVIEW_SHEET_NAME, row_data)
    await database.log_survey_completion('exit', user_id, restaurant_code)
    await database.deactivate_employee(user_id)
    await remove_exit_jobs(user_id, context)

    admin_message = (
        f"🚶‍♂️ <b>Сотрудник прошел Exit-интервью</b>\n\n"
        f"<b>Имя:</b> {html.escape(user_name)}\n"
        f"<b>Ресторан:</b> {html.escape(data.get('exit_restaurant', 'N/A'))}\n"
        f"<b>Должность:</b> {html.escape(data.get('exit_position', 'N/A'))}\n"
        f"<b>Стаж:</b> {html.escape(data.get('exit_duration', 'N/A'))}\n\n"
        f"<b>Основная причина ухода:</b>\n<pre>{html.escape(data.get('exit_reason', 'N/A'))}</pre>\n\n"
        f"<i>Сотрудник автоматически помечен как неактивный. Данные записаны в Google-таблицу.</i>"
    )
    recipients = set(settings.ADMIN_IDS)
    if restaurant_code:
        director_ids = await database.get_directors_for_restaurant(restaurant_code)
        recipients.update(director_ids)

    for recipient_id in recipients:
        try:
            await context.bot.send_message(recipient_id, admin_message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send exit interview summary to recipient {recipient_id}: {e}")


    final_text = "✅ <b>Опрос завершен!</b>\n\nБольшое спасибо тебе за уделенное время и честные ответы! 🙏\nЖелаем тебе огромных успехов на новом пути! ✨"
    await send_or_edit_message(update, context, final_text)

    await asyncio.sleep(0.5)
    await context.bot.send_sticker(chat_id=user_id, sticker=stickers.SUCCESS_DOG)

    context.user_data.clear()
    return ConversationHandler.END


chat_member_handler = ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER)
quit_clarification_handler = CallbackQueryHandler(
    handle_quit_clarification, pattern=f"^({settings.CALLBACK_CONFIRM_QUIT}|{settings.CALLBACK_DECLINE_QUIT})$"
)

exit_interview_conversation_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_exit_interview_callback, pattern=f"^{settings.CALLBACK_START_EXIT}$")],
    states={
        ExitState.RESTAURANT: [CallbackQueryHandler(exit_restaurant_chosen, pattern="^res_")],
        ExitState.POSITION: [CallbackQueryHandler(exit_position_chosen, pattern="^exit_pos_")],
        ExitState.DURATION: [CallbackQueryHandler(exit_duration_chosen, pattern="^dur_")],
        ExitState.AWAITING_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, exit_reason_received)],
        ExitState.AWAITING_IMPROVEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, exit_improvement_received)],
        ExitState.LEADERSHIP: [CallbackQueryHandler(exit_leadership_rated, pattern="^rate_")],
        ExitState.TRAINING: [CallbackQueryHandler(exit_training_received, pattern="^train_")],
        ExitState.FEEDBACK: [CallbackQueryHandler(exit_feedback_received, pattern="^feed_")],
        ExitState.AWAITING_COMMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, exit_comments_received)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="exit_interview_conv",
    persistent=True,
    per_message=False,
    conversation_timeout=settings.CONVERSATION_TIMEOUT_SECONDS
)