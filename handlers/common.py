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
    remove_keyboard_from_previous_message,
    send_transient_message,
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
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    logger.info(f"User {user.id} ({user.full_name}) cancelled the conversation.")

    if context.user_data:
        context.user_data.clear()

    try:
        if update.callback_query:
            await update.callback_query.answer("Действие отменено.")

        from handlers.main_menu import start
        from handlers.admin import admin_panel_start

        if user.id in settings.ADMIN_IDS:
            await admin_panel_start(update, context)
        else:
            await start(update, context)

    except Forbidden:
        return await handle_blocked_user(user.id, context)
    except Exception as e:
        logger.warning(f"Could not properly execute cancel for user {user.id}: {e}")

    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Forbidden):
        logger.warning(f"Caught a Forbidden error in the global error handler: {context.error}")
        if update and isinstance(update, Update) and update.effective_user:
            await handle_blocked_user(update.effective_user.id, context)
        return

    logger.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_json() if isinstance(update, Update) else str(update)

    user_info = "N/A"
    if update and isinstance(update, Update) and update.effective_user:
        user = update.effective_user
        user_info = f"{user.full_name} (ID: {user.id}, @{user.username})"

    message = (
        f"🆘 <b>ОШИБКА В БОТЕ</b> 🆘\n\n"
        f"<b>Пользователь:</b> {html.escape(user_info)}\n"
        f"<b>Ошибка:</b> <code>{html.escape(str(context.error))}</code>\n\n"
        f"<b>Traceback:</b>\n"
        f"<pre>{html.escape(tb_string[:3500])}</pre>\n\n"
        f"<b>Update:</b>\n"
        f"<pre>{html.escape(update_str)[:400]}</pre>"
    )

    if settings.ADMIN_IDS:
        for admin_id in settings.ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.critical(f"CRITICAL: Failed to send error notification to admin {admin_id}: {e}")

    if update and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ой, что-то пошло не по плану... 🛠️\n\n"
                     "Наша команда уже получила уведомление и разбирается в ситуации. "
                     "Пожалуйста, попробуйте начать заново с помощью команды /start."
            )
        except Exception as e:
            logger.error(f"Failed to send user-facing error message to {update.effective_chat.id}: {e}")


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