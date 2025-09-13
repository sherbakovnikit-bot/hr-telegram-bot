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
from telegram.error import BadRequest, Forbidden

from models import DirectorState
from core import settings, database
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_new_menu_message,
    send_or_edit_message,
    get_id_from_input,
    set_user_commands,
)
from utils.keyboards import (
    get_director_menu_keyboard,
    get_director_restaurant_management_keyboard,
    RESTAURANT_OPTIONS,
)
from handlers.common import cancel

logger = logging.getLogger(__name__)


async def director_panel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    user = update.effective_user
    if update.callback_query:
        await safe_answer_callback_query(update.callback_query)

    director_restaurants = await database.get_director_restaurants(user.id)
    if not director_restaurants:
        await send_or_edit_message(update, context, "Ошибка: за вами не закреплено ни одного ресторана.")
        return ConversationHandler.END

    keyboard = get_director_menu_keyboard(director_restaurants)
    text = f"Ciao, {html.escape(user.first_name)}! 👋\n\nЭто панель управляющего. Выберите ресторан для управления:"

    await send_new_menu_message(context, user.id, text, keyboard)
    return DirectorState.CHOOSE_RESTAURANT


async def director_restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    res_code = query.data.split('_')[-1]
    context.user_data['director_res_code'] = res_code

    res_name = next(
        (res['name'] for res in await database.get_director_restaurants(query.from_user.id) if res['code'] == res_code),
        res_code)
    context.user_data['director_res_name'] = res_name

    keyboard = get_director_restaurant_management_keyboard(res_code)
    text = f"Управление рестораном: <b>{html.escape(res_name)}</b>"

    await send_or_edit_message(update, context, text, keyboard)
    return DirectorState.MANAGE_RESTAURANT


async def add_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    res_code = context.user_data.get('director_res_code')
    res_name = context.user_data.get('director_res_name')

    if not res_code or not res_name:
        await query.answer("Ошибка: не выбран ресторан. Пожалуйста, начните заново.", show_alert=True)
        return await director_panel_start(update, context)

    text = (f"Ресторан: «{res_name}».\n\n"
            f"<b>Добавление менеджера:</b>\nПерешлите сообщение от будущего менеджера, введите его ID или @username.")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data=f"dir_res_{res_code}")
    ]])
    await send_or_edit_message(update, context, text, keyboard)
    return DirectorState.AWAIT_ADD_ID


async def add_manager_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    try:
        await update.message.delete()
    except (BadRequest, Forbidden):
        pass

    await send_or_edit_message(update, context, "Проверяю данные...")
    user_id_to_add = await get_id_from_input(update, context)
    if user_id_to_add is None:
        return DirectorState.AWAIT_ADD_ID

    res_code = context.user_data.get('director_res_code')
    res_name = context.user_data.get('director_res_name')

    if not res_code or not res_name:
        await director_panel_start(update, context)
        return ConversationHandler.END

    if await database.is_manager_in_restaurant(user_id_to_add, res_code):
        text = "⚠️ Этот пользователь уже является менеджером данного ресторана."
    else:
        try:
            user_chat = await context.bot.get_chat(user_id_to_add)
            full_name = f"{user_chat.first_name or ''} {user_chat.last_name or ''}".strip() or "Имя не получено"
            await database.add_manager(user_id_to_add, res_code, full_name, user_chat.username)
            await set_user_commands(user_id_to_add, context.bot)
            text = f"✅ <b>{html.escape(full_name)}</b> успешно назначен(а) менеджером в «{res_name}»."
            logger.info(f"Director {update.effective_user.id} added manager {user_id_to_add} to restaurant {res_code}")
        except (BadRequest, Forbidden) as e:
            text = f"❌ Ошибка: не удалось найти пользователя {user_id_to_add} или он не запускал бота. ({e})"

    await send_or_edit_message(update, context, text)
    query_data = f"dir_res_{res_code}"
    update.callback_query = Update.from_dict(
        {'callback_query': {'data': query_data, 'from': {'id': update.effective_user.id}}}).callback_query
    return await director_restaurant_chosen(update, context)


async def remove_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    res_code = context.user_data.get('director_res_code')
    res_name = context.user_data.get('director_res_name')

    if not res_code or not res_name:
        await query.answer("Ошибка: не выбран ресторан. Пожалуйста, начните заново.", show_alert=True)
        return await director_panel_start(update, context)

    await send_or_edit_message(update, context, "Загрузка списка менеджеров...", None)

    all_managers = await database.get_all_managers_by_restaurant()
    managers_in_res = all_managers.get(res_code, [])

    if not managers_in_res:
        await query.answer("В этом ресторане нет менеджеров для удаления.", show_alert=True)
        query.data = f"dir_res_{res_code}"
        return await director_restaurant_chosen(update, context)

    buttons = []
    for manager in managers_in_res:
        user_mention = manager.get('full_name', f"User {manager['user_id']}")
        button_text = f"❌ {html.escape(user_mention)}"
        buttons.append([InlineKeyboardButton(button_text,
                                             callback_data=f"dir_rem_mgr_select_{manager['user_id']}_{res_code}")])

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"dir_res_{res_code}")])

    await send_or_edit_message(update, context, f"Выберите менеджера для удаления из «{res_name}»:",
                               InlineKeyboardMarkup(buttons))
    return DirectorState.AWAIT_REMOVAL_ID


async def remove_manager_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> DirectorState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    _, _, _, _, user_id_str, res_code = query.data.split('_')
    user_id = int(user_id_str)

    await database.remove_manager(user_id, res_code)
    await set_user_commands(user_id, context.bot)
    await query.answer("Менеджер удален.")

    context.user_data['director_res_code'] = res_code
    res_name = next((name for name, r_code in RESTAURANT_OPTIONS if r_code.endswith(res_code)), res_code)
    context.user_data['director_res_name'] = res_name
    return await remove_manager_start(update, context)


director_conversation_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(director_panel_start, pattern="^director_panel_start$"),
    ],
    states={
        DirectorState.CHOOSE_RESTAURANT: [
            CallbackQueryHandler(director_restaurant_chosen, pattern="^dir_res_")
        ],
        DirectorState.MANAGE_RESTAURANT: [
            CallbackQueryHandler(add_manager_start, pattern="^dir_add_mgr_start_"),
            CallbackQueryHandler(remove_manager_start, pattern="^dir_rem_mgr_start_"),
            CallbackQueryHandler(director_panel_start, pattern="^director_panel_start$")
        ],
        DirectorState.AWAIT_ADD_ID: [
            MessageHandler(filters.TEXT & ~filters.COMMAND | filters.FORWARDED, add_manager_id_received),
            CallbackQueryHandler(director_restaurant_chosen, pattern="^dir_res_")
        ],
        DirectorState.AWAIT_REMOVAL_ID: [
            CallbackQueryHandler(remove_manager_selected, pattern="^dir_rem_mgr_select_"),
            CallbackQueryHandler(director_restaurant_chosen, pattern="^dir_res_")
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="director_conv",
    persistent=True,
    per_user=True,
    per_chat=True,
    per_message=False,
)