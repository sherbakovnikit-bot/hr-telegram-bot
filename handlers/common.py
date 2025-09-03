import html
import logging
import time
import traceback

logger = logging.getLogger(__name__)

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden

from core import settings, database
from utils.helpers import (
    get_user_data_from_update,
    add_user_to_interacted,
    remove_keyboard_from_previous_message,
    send_transient_message,
    safe_answer_callback_query,
    send_new_menu_message
)


async def handle_blocked_user(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles logic when a user has blocked the bot."""
    logger.warning(f"User {user_id} has blocked the bot. Aborting interaction and cleaning up data.")
    await database.delete_user_data(user_id)
    interacted_set = context.bot_data.setdefault("users_interacted", set())
    interacted_set.discard(user_id)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id, user_name, _ = get_user_data_from_update(update)
    logger.info(f"User {user_name} ({user_id}) cancelled the conversation.")

    if context.user_data:
        context.user_data.clear()

    try:
        await remove_keyboard_from_previous_message(context, user_id)
        if update.effective_message:
            await update.effective_message.reply_text(
                "Действие отменено.", reply_markup=ReplyKeyboardRemove()
            )
    except Forbidden:
        return await handle_blocked_user(user_id, context)
    except Exception as e:
        logger.warning(f"Could not properly execute cancel for user {user_id}: {e}")

    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Forbidden):
        logger.warning(f"Caught a Forbidden error in the global error handler: {context.error}")
        if update and isinstance(update, Update) and update.effective_user:
            await handle_blocked_user(update.effective_user.id, context)
        return

    logger.error("Exception while handling an update:", exc_info=context.error)

    if update and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ой, что-то пошло не по плану... 🛠️\n\nНаша команда уже получила сигнал и разбирается в ситуации. Пожалуйста, попробуй начать заново через пару минут.\n\nЕсли проблема повторяется, можно отменить действие командой /cancel."
            )
        except Exception as e:
            logger.error(f"Failed to send user-facing error message: {e}")

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    update_str = update.to_json() if isinstance(update, Update) else str(update)
    user_info = "N/A"
    if context.user_data and 'chat_id' in context.user_data:
        user_info = f"User/Chat ID: {context.user_data['chat_id']}"

    message = (
        f"🆘 <b>Ой, в боте что-то сломалось!</b> 🆘\n\n"
        f"<b>Пользователь:</b> {html.escape(user_info)}\n"
        f"<b>Тип ошибки:</b> <code>{html.escape(type(context.error).__name__)}</code>\n"
        f"<b>Сообщение:</b>\n<pre>{html.escape(str(context.error))}</pre>\n\n"
        f"<b>Трассировка (кратко):</b>\n"
        f"<pre>{html.escape(''.join(tb_list[-3:]))[:1000]}</pre>\n\n"
        f"<b>Update (сокращенно):</b>\n"
        f"<pre>{html.escape(update_str)[:1000]}</pre>"
    )

    if settings.ADMIN_IDS:
        for admin_id in settings.ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.critical(f"CRITICAL: Failed to send error notification to admin {admin_id}: {e}")


async def update_timestamp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(context.application, "bot_data") and 'last_telegram_update_ts' in context.application.bot_data:
        context.application.bot_data['last_telegram_update_ts'] = time.time()


async def prompt_to_use_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        try:
            await update.message.delete()
        except (BadRequest, Forbidden):
            pass
    await send_transient_message(context, update.effective_chat.id,
                                 "Пожалуйста, используй кнопки для ответа. Они чуть выше ⬆️")