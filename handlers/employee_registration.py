import html
import logging

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from models import EmployeeRegistrationState
from core import settings, database
from utils.keyboards import RESTAURANT_OPTIONS, build_inline_keyboard
from utils.helpers import send_or_edit_message, set_user_commands
from handlers.common import cancel

logger = logging.getLogger(__name__)


async def start_employee_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    context.user_data.clear()

    logger.info(f"User {user.id} started employee registration flow.")

    keyboard = build_inline_keyboard(RESTAURANT_OPTIONS, columns=2)
    text = (
        "Ciao! 👋 Добро пожаловать в команду «Марчеллис».\n\n"
        "Чтобы получить доступ к внутренним опросам и важным уведомлениям, пожалуйста, подтверди, в каком ресторане ты работаешь."
    )

    await send_or_edit_message(update, context, text, keyboard)
    return EmployeeRegistrationState.CHOOSE_RESTAURANT


async def restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    restaurant_code_suffix = query.data.replace("res_", "")
    restaurant_name = next((name for name, code in RESTAURANT_OPTIONS if code == query.data), "Неизвестный ресторан")

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or f"User {user.id}"

    await database.add_employee(user.id, full_name, restaurant_code_suffix)
    logger.info(
        f"New employee {user.id} ({full_name}) registered and activated for restaurant {restaurant_code_suffix}.")

    await set_user_commands(user.id, context.bot)

    text = (
        f"✅ Отлично, {html.escape(user.first_name)}!\n\n"
        f"Ты зарегистрирован(а) как сотрудник ресторана «<b>{html.escape(restaurant_name)}</b>».\n\n"
        "Теперь тебе доступна команда /surveys для прохождения опросов. Спасибо!"
    )
    await send_or_edit_message(update, context, text, None)

    context.user_data.clear()
    return ConversationHandler.END


employee_registration_handler = ConversationHandler(
    entry_points=[CommandHandler("join", start_employee_registration)],
    states={
        EmployeeRegistrationState.CHOOSE_RESTAURANT: [
            CallbackQueryHandler(restaurant_chosen, pattern="^res_")
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="employee_reg_conv",
    persistent=True,
    per_user=True,
    per_chat=True,
    per_message=False,
)