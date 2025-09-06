import asyncio
import html
import logging
from typing import Optional, Tuple, List, Any
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    Update,
    InlineKeyboardMarkup,
    CallbackQuery,
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChannel,
    InlineKeyboardButton,
    Message,
    Bot,
    BotCommand,
    BotCommandScopeChat,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from core import settings, database

logger = logging.getLogger(__name__)

try:
    TIMEZONE = ZoneInfo(settings.MOSCOW_TIMEZONE)
except ZoneInfoNotFoundError:
    logger.warning(f"Timezone '{settings.MOSCOW_TIMEZONE}' not found. Falling back to UTC.")
    TIMEZONE = ZoneInfo("UTC")


async def cleanup_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Удаляет последние N сообщений бота, чтобы очистить чат."""
    message_ids = context.user_data.pop('last_bot_messages', [])
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except (BadRequest, Forbidden):
            pass


def add_bot_message_to_cleanup_list(context: ContextTypes.DEFAULT_TYPE, message: Message):
    """Добавляет ID сообщения в список для последующей очистки."""
    if 'last_bot_messages' not in context.user_data:
        context.user_data['last_bot_messages'] = []
    context.user_data['last_bot_messages'].append(message.message_id)
    context.user_data['last_bot_messages'] = context.user_data['last_bot_messages'][-5:]


def get_now() -> datetime:
    return datetime.now(TIMEZONE)


def build_inline_keyboard(buttons: List[Tuple[str, str]], columns: int) -> InlineKeyboardMarkup:
    layout = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
    keyboard = [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in layout]
    return InlineKeyboardMarkup(keyboard)


def get_user_data_from_update(update: Optional[Update]) -> Tuple[int, str, Optional[str]]:
    user = None
    chat_id = 0

    if update:
        if update.effective_user:
            user = update.effective_user
        elif hasattr(update, 'callback_query') and update.callback_query and update.callback_query.from_user:
            user = update.callback_query.from_user
        elif hasattr(update, 'my_chat_member') and update.my_chat_member and update.my_chat_member.from_user:
            user = update.my_chat_member.from_user
        elif hasattr(update, 'chat_member') and update.chat_member and update.chat_member.new_chat_member:
             user = update.chat_member.new_chat_member.user

    if user:
        user_id = user.id
        first_name = user.first_name
        full_name = f"{html.escape(first_name or '')} {html.escape(user.last_name or '')}".strip()
        user_display_name = f"@{user.username}" if user.username else full_name or f"User_{user_id}"
        return user_id, user_display_name, first_name

    if update and update.effective_chat:
        user_id = update.effective_chat.id
        return user_id, f"Chat_{user_id}", None

    logger.warning("Could not determine user or chat ID from update.")
    return 0, "Unknown User", None


async def set_user_commands(user_id: int, bot: Bot):
    if user_id == 0:
        return
    commands = []
    if user_id in settings.ADMIN_IDS:
        commands = [BotCommand("start", "⭐ Панель Администратора")]
    elif await database.is_user_a_manager(user_id):
        commands = [BotCommand("start", "🏠 Главное меню менеджера")]
    else:
        commands = []

    try:
        current_commands = await bot.get_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
        if [c.to_dict() for c in commands] != [c.to_dict() for c in current_commands]:
            await bot.set_my_commands(commands=commands, scope=BotCommandScopeChat(chat_id=user_id))
            logger.info(f"Updated commands for user {user_id}.")
    except (BadRequest, Forbidden) as e:
        logger.warning(f"Could not set commands for user {user_id}: {e}")


async def add_user_to_interacted(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if user_id == 0: return
    interacted_set = context.bot_data.setdefault("users_interacted", set())
    if user_id not in interacted_set:
        interacted_set.add(user_id)
        logger.info(f"Added user {user_id} to interacted set. Current size: {len(interacted_set)}")
        if context.application.persistence:
            try:
                await context.application.persistence.flush()
            except Exception as e:
                logger.error(f"Error flushing persistence after adding user: {e}")


async def safe_answer_callback_query(query: Optional[CallbackQuery]):
    if not query: return
    try:
        await query.answer()
    except BadRequest:
        pass
    except Forbidden:
        logger.warning(f"Forbidden to answer callback query {query.id}. Bot might be blocked.")
    except Exception as e:
        logger.error(f"Unexpected error answering callback query {query.id}: {e}", exc_info=True)


async def send_transient_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    try:
        message = await context.bot.send_message(chat_id=chat_id, text=text)
        add_bot_message_to_cleanup_list(context, message)
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Could not send transient message to {chat_id}: {e}")


async def send_or_edit_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None
):
    chat_id = update.effective_chat.id
    user_message_id = update.message.message_id if update.message else None
    active_message_id = context.user_data.get(settings.ACTIVE_MESSAGE_ID_KEY)
    new_message = None

    if active_message_id:
        try:
            new_message = await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=active_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except (BadRequest, Forbidden, TelegramError):
            active_message_id = None

    if not active_message_id:
        try:
            await cleanup_chat(context, chat_id)
            new_message = await context.bot.send_message(
                chat_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        except (Forbidden, TelegramError) as send_e:
             logger.error(f"Failed to send new message to chat {chat_id}: {send_e}")
             return

    if user_message_id:
        try:
            await context.bot.delete_message(chat_id, user_message_id)
        except (BadRequest, Forbidden):
            pass

    if new_message:
        context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = new_message.message_id
        add_bot_message_to_cleanup_list(context, new_message)


async def send_new_menu_message(
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup
):
    await cleanup_chat(context, chat_id)
    try:
        new_message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = new_message.message_id
        add_bot_message_to_cleanup_list(context, new_message)
    except (Forbidden, BadRequest) as e:
        logger.error(f"Failed to send new menu message to {chat_id}: {e}")


async def get_id_from_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    message = update.message
    if message.forward_origin:
        origin = message.forward_origin
        if isinstance(origin, MessageOriginUser): return origin.sender_user.id
        await message.reply_text("Пожалуйста, перешлите сообщение от пользователя, а не из канала или от скрытого пользователя.")
        return None
    text = message.text
    if not text: return None
    if text.isdigit(): return int(text)
    if text.startswith('@'):
        try:
            chat = await context.bot.get_chat(text)
            return chat.id
        except (BadRequest, Forbidden):
            await message.reply_text(f"Не удалось найти пользователя с ником {text}.")
            return None
    await message.reply_text("Неверный формат. Нужен ID, @username или пересланное сообщение.")
    return None


async def add_to_sheets_queue(queue_name: str, data: List[Any]):
    if not data: return
    try:
        await database.add_to_sheets_db_queue(queue_name, data)
        logger.info(f"Added 1 item to GSheets queue for sheet '{queue_name}'.")
    except Exception as e:
        logger.error(f"Failed to add data to DB queue '{queue_name}': {e}", exc_info=True)


def format_user_for_sheets(user_id: int, full_name: str, username: Optional[str] = None) -> str:
    display_name = full_name
    if username:
        display_name += f" (@{username})"
    return f'=HYPERLINK("tg://user?id={user_id}"; "{display_name}")'