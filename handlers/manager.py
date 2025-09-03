import html
import logging
import time
import asyncio

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ParseMode

from models import ManagerRegistrationState
from core import settings, database, stickers
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_or_edit_message,
    send_transient_message,
    add_user_to_interacted,
    set_user_commands
)
from utils.keyboards import (
    RESTAURANT_OPTIONS,
    build_inline_keyboard
)
from handlers.common import cancel, prompt_to_use_button

logger = logging.getLogger(__name__)

AWAIT_REJECTION_REASON = -1


async def register_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | ManagerRegistrationState:
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    await add_user_to_interacted(user.id, context)
    context.user_data.clear()

    if user.id in settings.ADMIN_IDS:
        if update.message:
            await update.message.reply_text(
                "Привет! Как администратор, ты уже имеешь все права. Для управления используй команду /admin.",
                reply_markup=ReplyKeyboardRemove()
            )
        return ConversationHandler.END

    keyboard = build_inline_keyboard(RESTAURANT_OPTIONS, columns=2)
    text = ("Привет! 👋 Добро пожаловать в систему регистрации менеджеров.\n\n"
            "Пожалуйста, выбери ресторан, за которым ты будешь закреплен(а), чтобы получать анкеты кандидатов 👇")

    if update.message:
        sent_message = await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = sent_message.message_id
    elif update.callback_query:
        await send_or_edit_message(update, context, text, keyboard)

    context.user_data['chat_id'] = update.effective_chat.id

    return ManagerRegistrationState.CHOOSE_RESTAURANT


async def restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ManagerRegistrationState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if not query or not query.data:
        return ConversationHandler.END

    restaurant_code_suffix = query.data.replace("res_", "")
    restaurant_name = next((name for name, code in RESTAURANT_OPTIONS if code == query.data), "Неизвестный ресторан")

    context.user_data['reg_restaurant_code'] = restaurant_code_suffix
    context.user_data['reg_restaurant_name'] = restaurant_name

    text = "Отлично. Теперь, пожалуйста, напиши свои настоящие <b>Фамилию и Имя</b>."
    await send_or_edit_message(update, context, text)
    return ManagerRegistrationState.AWAIT_FULL_NAME


async def full_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    full_name = update.message.text
    if len(full_name.split()) < 2:
        await send_transient_message(context, update.effective_chat.id, "Пожалуйста, введи и Фамилию, и Имя.")
        await update.message.delete()
        return ManagerRegistrationState.AWAIT_FULL_NAME

    user = update.effective_user
    user_id = user.id
    username = user.username
    restaurant_code = context.user_data['reg_restaurant_code']
    restaurant_name = context.user_data['reg_restaurant_name']

    await database.add_pending_manager(
        user_id, restaurant_code, restaurant_name, full_name, username, time.time()
    )

    text = ("✅ Отлично! Твоя заявка принята и отправлена администратору на рассмотрение.\n\n"
            "Как только ее одобрят, ты получишь уведомление. Спасибо!")
    await send_or_edit_message(update, context, text)

    if settings.ADMIN_IDS:
        approval_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"{settings.CALLBACK_MGR_APPROVE_PREFIX}{user_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"{settings.CALLBACK_MGR_REJECT_PREFIX}{user_id}")
            ]
        ])
        message = (
            f"🔔 <b>Заявка на регистрацию менеджера</b> 🔔\n\n"
            f"Пользователь: <b>{html.escape(full_name)} (@{username})</b> (<code>{user_id}</code>)\n"
            f"Хочет стать менеджером ресторана: <b>{html.escape(restaurant_name)}</b>"
        )
        for admin_id in settings.ADMIN_IDS:
            try:
                await context.bot.send_sticker(chat_id=admin_id, sticker=stickers.CONTACT_MANAGER)
                await asyncio.sleep(0.3)
                await context.bot.send_message(admin_id, message, reply_markup=approval_keyboard,
                                               parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send manager approval request to admin {admin_id}: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def handle_manager_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if not query or not query.data or not query.from_user:
        return

    admin_user = query.from_user
    approve_prefix = settings.CALLBACK_MGR_APPROVE_PREFIX
    reject_prefix = settings.CALLBACK_MGR_REJECT_PREFIX

    if query.data.startswith(approve_prefix):
        target_user_id = int(query.data[len(approve_prefix):])

        pending_request = await database.get_pending_manager(target_user_id)
        if not pending_request:
            await query.edit_message_text(f"{query.message.text}\n\n<i>Заявка уже была обработана.</i>",
                                          parse_mode=ParseMode.HTML, reply_markup=None)
            await query.answer("Заявка уже была обработана.", show_alert=True)
            return

        restaurant_code = pending_request['restaurant_code']
        restaurant_name = pending_request['restaurant_name']
        full_name = pending_request['full_name']
        username = pending_request['username']

        await database.remove_pending_manager(target_user_id)
        await database.add_manager(target_user_id, restaurant_code, full_name, username)

        logger.info(f"Admin {admin_user.id} approved manager {target_user_id} for restaurant {restaurant_code}.")
        await context.bot.send_message(target_user_id,
                                       f"🎉 Поздравляем! Твоя заявка на роль менеджера в ресторане «{restaurant_name}» одобрена. Теперь ты будешь получать анкеты кандидатов.")
        await query.edit_message_text(
            f"✅ <b>Заявка ОДОБРЕНА</b>\n\n"
            f"Пользователь: <b>{html.escape(full_name)} (@{username})</b>\n"
            f"Ресторан: <b>{html.escape(restaurant_name)}</b>\n"
            f"<i>Обработал(а): {admin_user.mention_html()}</i>",
            parse_mode=ParseMode.HTML, reply_markup=None
        )
        await set_user_commands(target_user_id, context.bot)

    elif query.data.startswith(reject_prefix):
        target_user_id = int(query.data[len(reject_prefix):])

        if not await database.get_pending_manager(target_user_id):
            await query.edit_message_text(f"{query.message.text}\n\n<i>Заявка уже была обработана.</i>",
                                          parse_mode=ParseMode.HTML, reply_markup=None)
            await query.answer("Заявка уже была обработана.", show_alert=True)
            return

        context.user_data['rejection_target_id'] = target_user_id
        await query.edit_message_text(
            "Пожалуйста, укажите причину отклонения. Это сообщение будет отправлено пользователю.", reply_markup=None)
        return AWAIT_REJECTION_REASON

    return ConversationHandler.END


async def rejection_reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reason = update.message.text
    admin_user = update.effective_user
    target_user_id = context.user_data.pop('rejection_target_id', None)

    if not target_user_id:
        await update.message.reply_text("Ошибка: не найден ID пользователя для отклонения.")
        return ConversationHandler.END

    pending_request = await database.get_pending_manager(target_user_id)
    if not pending_request:
        await update.message.reply_text("Заявка уже была обработана другим администратором.")
        return ConversationHandler.END

    restaurant_name = pending_request['restaurant_name']
    full_name = pending_request['full_name']
    username = pending_request['username']

    await database.remove_pending_manager(target_user_id)

    logger.info(f"Admin {admin_user.id} rejected manager {target_user_id} with reason: {reason}")

    await context.bot.send_message(
        target_user_id,
        f"😔 К сожалению, твоя заявка на роль менеджера в ресторане «{restaurant_name}» была отклонена.\n\n"
        f"<b>Причина:</b> {html.escape(reason)}"
    )

    await update.message.reply_text(
        f"❌ <b>Заявка ОТКЛОНЕНА</b>\n\n"
        f"Пользователь: <b>{html.escape(full_name)} (@{username})</b>\n"
        f"Ресторан: <b>{html.escape(restaurant_name)}</b>\n"
        f"Причина: {html.escape(reason)}\n"
        f"<i>Обработал(а): {admin_user.mention_html()}</i>",
        parse_mode=ParseMode.HTML
    )

    await set_user_commands(target_user_id, context.bot)
    return ConversationHandler.END


manager_registration_handler = ConversationHandler(
    entry_points=[
        CommandHandler("register_manager", register_manager_start),
        CallbackQueryHandler(handle_manager_approval,
                             pattern=f"^{settings.CALLBACK_MGR_APPROVE_PREFIX}|^({settings.CALLBACK_MGR_REJECT_PREFIX})")
    ],
    states={
        ManagerRegistrationState.CHOOSE_RESTAURANT: [
            CallbackQueryHandler(restaurant_chosen, pattern="^res_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button),
        ],
        ManagerRegistrationState.AWAIT_FULL_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, full_name_received)
        ],
        AWAIT_REJECTION_REASON: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rejection_reason_received)
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="manager_reg_conv",
    persistent=True,
    per_message=False,
)