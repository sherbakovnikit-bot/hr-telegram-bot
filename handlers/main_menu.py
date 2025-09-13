import html
import logging
import asyncio

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden

from models import MainMenuState, FeedbackState, ManagerFeedbackState
from core import settings, database, stickers
from utils.helpers import (
    safe_answer_callback_query, add_user_to_interacted,
    get_user_data_from_update, send_new_menu_message, send_or_edit_message,
    set_user_commands
)
from utils.keyboards import get_manager_menu_keyboard, get_pending_feedback_keyboard
from handlers.common import handle_blocked_user, cancel
from handlers.director import director_panel_start

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    if update.callback_query:
        await safe_answer_callback_query(update.callback_query)

    active_conv_names = [
        'recruitment_conv', 'onboarding_conv', 'exit_interview_conv',
        'climate_survey_conv', 'manager_reg_conv', 'director_conv'
    ]
    current_conversations = context.user_data.get('conversations', {})
    is_in_another_conv = any(current_conversations.get(name) for name in active_conv_names)

    if is_in_another_conv:
        logger.info(f"User {user.id} tried to use /start while in an active conversation.")
        if update.effective_message:
            await update.effective_message.reply_text(
                "Кажется, вы сейчас в процессе заполнения анкеты. "
                "Чтобы прервать и начать заново, пожалуйста, используйте команду /cancel.",
                reply_markup=ReplyKeyboardRemove()
            )
        return ConversationHandler.END

    try:
        await set_user_commands(user.id, context.bot)
        await add_user_to_interacted(user.id, context)

        is_director = await database.is_user_a_director(user.id)
        if is_director:
            return await director_panel_start(update, context)

        is_manager = await database.is_user_a_manager(user.id)
        if is_manager:
            return await show_manager_menu(update, context)

        await context.bot.send_sticker(chat_id=user.id, sticker=stickers.get_random_greeting())
        await send_or_edit_message(
            update,
            context,
            "Ciao! 👋 Я бот-помощник команды «Марчеллис».\n\n"
            "Если вы кандидат, пожалуйста, воспользуйтесь специальной ссылкой или QR-кодом от менеджера, чтобы начать анкетирование.\n\n"
            "Если у вас есть вопрос, предложение или вы хотите оставить отзыв — просто напишите его в следующем сообщении.",
        )
        return ConversationHandler.END

    except Forbidden:
        return await handle_blocked_user(user.id, context)
    except Exception as e:
        logger.error(f"Error in start handler for user {user.id}: {e}", exc_info=True)
        return ConversationHandler.END


async def show_manager_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user: return ConversationHandler.END

    await context.bot.send_sticker(chat_id=user.id, sticker=stickers.GREETING_WAITER)
    await asyncio.sleep(0.3)

    pending_feedback = await database.get_pending_feedback_for_manager(user.id)
    keyboard = get_manager_menu_keyboard(len(pending_feedback))
    text = f"Ciao, {html.escape(user.first_name)}! 👋\n\nЭто твое меню менеджера."

    await send_new_menu_message(context, user.id, text, keyboard)
    return MainMenuState.MAIN


async def handle_manager_feedback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await safe_answer_callback_query(query)
    user_id = query.from_user.id

    pending_tasks = await database.get_pending_feedback_for_manager(user_id)
    if not pending_tasks:
        await query.answer("У тебя нет кандидатов, ожидающих обратной связи.", show_alert=True)
        return MainMenuState.MAIN

    keyboard = get_pending_feedback_keyboard(pending_tasks)
    await send_or_edit_message(update, context, "Выбери кандидата, по которому хочешь оставить обратную связь:",
                               keyboard)

    return MainMenuState.AWAITING_FEEDBACK_CHOICE


async def handle_feedback_candidate_selection(update: Update,
                                              context: ContextTypes.DEFAULT_TYPE) -> ManagerFeedbackState:
    query = update.callback_query

    feedback_id = query.data.replace("fb_", "")
    context.user_data['feedback_id'] = feedback_id

    from handlers.manager_feedback_flow import start_manager_feedback_flow
    return await start_manager_feedback_flow(update, context)


async def start_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    logger.info(f"User {user.id} triggered /start or text message. Routing to general FEEDBACK flow.")

    text = ("Привет! 👋 Я бот-помощник команды «Марчеллис».\n\n"
            "Если у тебя есть предложение, вопрос или жалоба — просто напиши это в следующем сообщении, и я передам всё нашей команде.\n\n"
            "Чтобы отменить, отправь команду /cancel.")

    try:
        await context.bot.send_sticker(chat_id=user.id, sticker=stickers.QUESTION_DOG)
        await asyncio.sleep(0.5)

        await send_or_edit_message(update, context, text)

        return FeedbackState.AWAITING_FEEDBACK
    except Forbidden:
        return await handle_blocked_user(user.id, context)


async def receive_and_forward_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    feedback_text = update.message.text
    user_id, user_name, _ = get_user_data_from_update(update)
    logger.info(f"Received general feedback from {user_name}")

    if settings.ADMIN_IDS:
        message = (f"📩 <b>Новое сообщение от пользователя</b> 📩\n\n"
                   f"<b>От:</b> {html.escape(user_name)} (<code>{user_id}</code>)\n"
                   f"<b>Сообщение:</b>\n<pre>{html.escape(feedback_text)}</pre>")
        for admin_id in settings.ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, message, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to forward feedback to admin {admin_id}: {e}")

        await send_or_edit_message(update, context, "Спасибо! Мы получили твое сообщение и передали команде. 🙏")
        await asyncio.sleep(0.5)
        await context.bot.send_sticker(chat_id=user_id, sticker=stickers.FEEDBACK_SENT_DOG)
    else:
        await send_or_edit_message(update, context,
                                   "К сожалению, функция отправки сообщений сейчас не работает. Попробуй, пожалуйста, позже."
                                   )

    context.user_data.clear()
    return ConversationHandler.END

async def show_surveys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_employee = await database.get_employee_details(user_id)

    if not is_employee or not is_employee['is_active']:
        await update.message.reply_text("Эта команда доступна только для действующих сотрудников.")
        return

    buttons = []
    if not await database.is_survey_completed('climate', user_id):
        buttons.append([InlineKeyboardButton("🌡️ Пройти опрос по климату", callback_data=settings.CALLBACK_START_CLIMATE)])

    if not buttons:
        await update.message.reply_text("Для тебя пока нет доступных опросов. Спасибо за активность!")
    else:
        await update.message.reply_text("Доступные опросы:", reply_markup=InlineKeyboardMarkup(buttons))