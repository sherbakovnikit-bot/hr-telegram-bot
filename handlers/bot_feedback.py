import html
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)
from telegram.constants import ParseMode

from models import MainMenuState
from core import settings
from utils.helpers import get_user_data_from_update, add_to_sheets_queue, get_now, safe_answer_callback_query, \
    send_or_edit_message
from handlers.common import cancel

logger = logging.getLogger(__name__)

SELECT_TYPE, AWAIT_DESCRIPTION = range(2)

FEEDBACK_TYPES = {
    "fb_bug": "Ошибка (Баг)",
    "fb_suggestion": "Предложение",
    "fb_other": "Другое"
}


async def start_feedback_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = query.message.message_id

    keyboard = [
        [InlineKeyboardButton("🐞 Ошибка (Баг)", callback_data="fb_bug")],
        [InlineKeyboardButton("💡 Предложение", callback_data="fb_suggestion")],
        [InlineKeyboardButton("📋 Другое", callback_data="fb_other")],
    ]

    text = ("Спасибо, что помогаете нам стать лучше! 🙏\n\n"
            "Пожалуйста, выберите тип вашей обратной связи:")

    await send_or_edit_message(update, context, text, InlineKeyboardMarkup(keyboard))
    return SELECT_TYPE


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await safe_answer_callback_query(query)

    feedback_type_code = query.data
    feedback_type_text = FEEDBACK_TYPES.get(feedback_type_code, "Не определено")
    context.user_data['feedback_type'] = feedback_type_text

    text = (f"Вы выбрали: <b>{feedback_type_text}</b>.\n\n"
            "Теперь, пожалуйста, опишите подробно вашу мысль в одном сообщении. "
            "Если это ошибка, опишите шаги для ее воспроизведения.")

    await send_or_edit_message(update, context, text)
    return AWAIT_DESCRIPTION


async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    description = update.message.text
    feedback_type = context.user_data.get('feedback_type', 'Не определено')
    user_id, user_name, _ = get_user_data_from_update(update)
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")

    row_data = [timestamp, user_name, user_id, feedback_type, description, "Новое"]
    await add_to_sheets_queue(settings.BOT_FEEDBACK_SHEET_NAME, row_data)

    text = "✅ Ваше сообщение принято! Спасибо за ваш вклад. Мы рассмотрим его в ближайшее время."
    await send_or_edit_message(update, context, text)

    context.user_data.clear()
    return ConversationHandler.END


feedback_submission_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_feedback_submission, pattern="^submit_bot_feedback$")],
    states={
        SELECT_TYPE: [CallbackQueryHandler(type_selected, pattern="^fb_")],
        AWAIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    name="bot_feedback_conv",
    per_user=True,
    per_chat=True,
    per_message=False,
)