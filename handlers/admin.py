import asyncio
import html
import logging
import math
from collections import defaultdict

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden

from models import AdminState
from core import settings, database
from utils.helpers import (
    safe_answer_callback_query,
    get_id_from_input,
    send_new_menu_message,
    send_or_edit_message,
    set_user_commands
)
from utils.keyboards import (
    RESTAURANT_OPTIONS,
    get_admin_menu_keyboard,
)

logger = logging.getLogger(__name__)


async def edit_admin_message(query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Failed to edit admin message: {e}")


async def admin_panel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    user = update.effective_user
    keyboard = get_admin_menu_keyboard()
    text = f"Ciao, {html.escape(user.first_name)}! 👋\n\nДобро пожаловать в панель администратора. Выбери действие:"

    if update.callback_query:
        await safe_answer_callback_query(update.callback_query)
        await edit_admin_message(update.callback_query, text, keyboard)
    else:
        if update.message:
            try:
                await update.message.delete()
            except (BadRequest, Forbidden):
                pass
        await send_new_menu_message(context, user.id, text, keyboard)

    return AdminState.MENU


async def manage_managers_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить менеджера", callback_data="admin_add_manager_start")],
        [InlineKeyboardButton("➖ Удалить менеджера (со списком)", callback_data="admin_remove_manager_start")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=settings.CALLBACK_ADMIN_BACK)],
    ])
    text = "Управление менеджерами:"
    await edit_admin_message(query, text, keyboard)
    return AdminState.MANAGE_MANAGERS


async def manage_directors_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить управляющего", callback_data="admin_add_director_start")],
        [InlineKeyboardButton("➖ Удалить управляющего (со списком)", callback_data="admin_remove_director_start")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=settings.CALLBACK_ADMIN_BACK)],
    ])
    text = "Управление управляющими:"
    await edit_admin_message(query, text, keyboard)
    return AdminState.MANAGE_DIRECTORS


async def manage_employees_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    button_rows_data = [RESTAURANT_OPTIONS[i:i + 2] for i in range(0, len(RESTAURANT_OPTIONS), 2)]
    keyboard_layout = [
        [InlineKeyboardButton(text, callback_data=f"list_emp_res_{data.split('_')[1]}_page_0") for text, data in
         row_data]
        for row_data in button_rows_data
    ]
    keyboard_layout.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data=settings.CALLBACK_ADMIN_BACK)])
    keyboard = InlineKeyboardMarkup(keyboard_layout)
    text = "👥 Выберите ресторан для просмотра и управления списком сотрудников:"
    await edit_admin_message(query, text, keyboard)
    return AdminState.CHOOSE_EMPLOYEE_RESTAURANT


async def show_employees_paginated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    parts = query.data.split('_')
    res_code_suffix = parts[3]
    page = int(parts[5])

    total_employees = await database.count_employees_in_restaurant(res_code_suffix)
    if total_employees == 0 and page == 0:
        await query.answer("В этом ресторане нет сотрудников.", show_alert=True)
        return await manage_employees_start(update, context)

    employees = await database.get_employees_paginated(res_code_suffix, page, settings.EMPLOYEES_PER_PAGE)
    res_name = next((name for name, code in RESTAURANT_OPTIONS if code.endswith(res_code_suffix)), res_code_suffix)
    total_pages = math.ceil(total_employees / settings.EMPLOYEES_PER_PAGE) if total_employees > 0 else 1

    buttons = []
    text = f"<b>Сотрудники «{html.escape(res_name)}»</b> (Стр. {page + 1}/{total_pages})"
    for emp in employees:
        status_icon = "✅" if emp['is_active'] else "⚪️"
        button_text = f"{status_icon} {html.escape(emp['full_name'] or f'User {emp['user_id']}')}"
        callback_data = f"adm_tgl_emp_{emp['user_id']}_res_{res_code_suffix}_page_{page}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("<< 1", callback_data=f"list_emp_res_{res_code_suffix}_page_0"))
        nav_buttons.append(
            InlineKeyboardButton(f"< {page}", callback_data=f"list_emp_res_{res_code_suffix}_page_{page - 1}"))
    if page + 1 < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(f"{page + 2} >", callback_data=f"list_emp_res_{res_code_suffix}_page_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton(f"{total_pages} >>",
                                                callback_data=f"list_emp_res_{res_code_suffix}_page_{total_pages - 1}"))

    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("⬅️ К выбору ресторана", callback_data="admin_manage_employees")])
    await edit_admin_message(query, text, InlineKeyboardMarkup(buttons))
    return AdminState.LIST_EMPLOYEES_PAGINATED


async def toggle_employee_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    parts = query.data.split('_')
    user_id = int(parts[3])
    await database.toggle_employee_status(user_id)
    await query.answer("Статус сотрудника изменен.")

    return await show_employees_paginated(update, context)


async def remove_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    await edit_admin_message(query, "Загрузка...", None)

    managers_map = await database.get_all_managers_by_restaurant()
    if not managers_map:
        await query.answer("Список менеджеров пуст. Некого удалять.", show_alert=True)
        return await manage_managers_start(update, context)

    buttons = []
    for res_code_suffix, managers in sorted(managers_map.items()):
        res_name = next((name for name, code in RESTAURANT_OPTIONS if code.endswith(res_code_suffix)), res_code_suffix)
        for manager in managers:
            user_mention = manager.get('full_name', f"User {manager['user_id']}")
            button_text = f"❌ {html.escape(user_mention)} ({html.escape(res_name)})"
            buttons.append([InlineKeyboardButton(button_text,
                                                 callback_data=f"admin_remove_mgr_{manager['user_id']}_{res_code_suffix}")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_manage_managers")])
    await edit_admin_message(query, "Выберите менеджера для удаления:", InlineKeyboardMarkup(buttons))
    return AdminState.AWAIT_REMOVAL_ID


async def remove_manager_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    _, _, _, user_id_str, res_code = query.data.split('_')
    await database.remove_manager(int(user_id_str), res_code)
    await set_user_commands(int(user_id_str), context.bot)
    await query.answer("Менеджер удален.")
    return await remove_manager_start(update, context)


async def add_manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    button_rows = [[InlineKeyboardButton(text, callback_data=data) for text, data in RESTAURANT_OPTIONS[i:i + 2]] for i
                   in range(0, len(RESTAURANT_OPTIONS), 2)]
    button_rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_manage_managers")])
    await edit_admin_message(query, "Шаг 1: Выберите ресторан для менеджера:", InlineKeyboardMarkup(button_rows))
    return AdminState.CHOOSE_ADD_RESTAURANT


async def add_restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data['admin_add_res_code'] = query.data.replace("res_", "")
    context.user_data['admin_add_res_name'] = next((n for n, c in RESTAURANT_OPTIONS if c == query.data), "?")
    text = (f"Ресторан: «{context.user_data['admin_add_res_name']}».\n\n"
            f"<b>Шаг 2:</b> Перешлите сообщение от будущего менеджера, введите его ID или @username.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад к выбору ресторана", callback_data="admin_add_manager_start")]])
    await edit_admin_message(query, text, keyboard)
    return AdminState.AWAIT_ADD_ID


async def add_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.delete()
    except (BadRequest, Forbidden):
        pass

    await send_or_edit_message(update, context, "Проверяю данные...")
    user_id_to_add = await get_id_from_input(update, context)
    if user_id_to_add is None:
        return AdminState.AWAIT_ADD_ID

    res_code = context.user_data.get('admin_add_res_code')
    res_name = context.user_data.get('admin_add_res_name')
    if not res_code:
        await admin_panel_start(update, context)
        return ConversationHandler.END

    if await database.is_manager_in_restaurant(user_id_to_add, res_code):
        text = "⚠️ Пользователь уже является менеджером этого ресторана."
    else:
        try:
            user_chat = await context.bot.get_chat(user_id_to_add)
            full_name = f"{user_chat.first_name or ''} {user_chat.last_name or ''}".strip() or "Имя не получено"
            await database.add_manager(user_id_to_add, res_code, full_name, user_chat.username)
            await set_user_commands(user_id_to_add, context.bot)
            text = f"✅ <b>{html.escape(full_name)}</b> успешно добавлен в менеджеры «{res_name}»."
            logger.info(f"Admin {update.effective_user.id} добавил менеджера {user_id_to_add} в ресторан {res_code}")
        except (BadRequest, Forbidden) as e:
            text = f"❌ Ошибка: не удалось найти пользователя {user_id_to_add} или он не запускал бота. ({e})"

    await send_or_edit_message(update, context, text)
    await admin_panel_start(update, context)
    return ConversationHandler.END


async def add_director_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    button_rows = [[InlineKeyboardButton(text, callback_data=data) for text, data in RESTAURANT_OPTIONS[i:i + 2]] for i
                   in range(0, len(RESTAURANT_OPTIONS), 2)]
    button_rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_manage_directors")])
    await edit_admin_message(query, "Шаг 1: Выберите ресторан для управляющего:", InlineKeyboardMarkup(button_rows))
    return AdminState.CHOOSE_ADD_DIRECTOR_RESTAURANT


async def add_director_restaurant_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data['admin_add_res_code'] = query.data.replace("res_", "")
    context.user_data['admin_add_res_name'] = next((n for n, c in RESTAURANT_OPTIONS if c == query.data), "?")
    text = (f"Ресторан: «{context.user_data['admin_add_res_name']}».\n\n"
            f"<b>Шаг 2:</b> Перешлите сообщение от будущего управляющего, введите его ID или @username.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад к выбору ресторана", callback_data="admin_add_director_start")]])
    await edit_admin_message(query, text, keyboard)
    return AdminState.AWAIT_ADD_DIRECTOR_ID


async def add_director_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.delete()
    except (BadRequest, Forbidden):
        pass

    await send_or_edit_message(update, context, "Проверяю данные...")
    user_id_to_add = await get_id_from_input(update, context)
    if user_id_to_add is None:
        return AdminState.AWAIT_ADD_DIRECTOR_ID

    res_code = context.user_data.get('admin_add_res_code')
    res_name = context.user_data.get('admin_add_res_name')
    if not res_code:
        await admin_panel_start(update, context)
        return ConversationHandler.END

    if await database.is_director_in_restaurant(user_id_to_add, res_code):
        text = "⚠️ Пользователь уже является управляющим этого ресторана."
    else:
        try:
            user_chat = await context.bot.get_chat(user_id_to_add)
            full_name = f"{user_chat.first_name or ''} {user_chat.last_name or ''}".strip() or "Имя не получено"
            await database.add_director(user_id_to_add, res_code, full_name, user_chat.username)
            await set_user_commands(user_id_to_add, context.bot)
            text = f"✅ <b>{html.escape(full_name)}</b> успешно добавлен в управляющие «{res_name}»."
            logger.info(f"Admin {update.effective_user.id} добавил управляющего {user_id_to_add} в ресторан {res_code}")
        except (BadRequest, Forbidden) as e:
            text = f"❌ Ошибка: не удалось найти пользователя {user_id_to_add} или он не запускал бота. ({e})"

    await send_or_edit_message(update, context, text)
    await admin_panel_start(update, context)
    return ConversationHandler.END


async def remove_director_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    await edit_admin_message(query, "Загрузка...", None)

    directors_map = await database.get_all_directors_by_restaurant()
    if not directors_map:
        await query.answer("Список управляющих пуст. Некого удалять.", show_alert=True)
        return await manage_directors_start(update, context)

    buttons = []
    for res_code_suffix, directors in sorted(directors_map.items()):
        res_name = next((name for name, code in RESTAURANT_OPTIONS if code.endswith(res_code_suffix)), res_code_suffix)
        for director in directors:
            user_mention = director.get('full_name', f"User {director['user_id']}")
            button_text = f"❌ {html.escape(user_mention)} ({html.escape(res_name)})"
            buttons.append([InlineKeyboardButton(button_text,
                                                 callback_data=f"admin_remove_dir_{director['user_id']}_{res_code_suffix}")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_manage_directors")])
    await edit_admin_message(query, "Выберите управляющего для удаления:", InlineKeyboardMarkup(buttons))
    return AdminState.AWAIT_DIRECTOR_REMOVAL_ID


async def remove_director_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    _, _, _, user_id_str, res_code = query.data.split('_')
    await database.remove_director(int(user_id_str), res_code)
    await set_user_commands(int(user_id_str), context.bot)
    await query.answer("Управляющий удален.")
    return await remove_director_start(update, context)


async def admin_list_pending_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    pending_tasks = await database.get_all_pending_feedback()
    if not pending_tasks:
        await query.answer("Нет кандидатов на рассмотрении.", show_alert=True)
        return await admin_panel_start(update, context)

    await safe_answer_callback_query(query)
    candidates_by_restaurant = defaultdict(list)
    for task in pending_tasks:
        candidates_by_restaurant[task['restaurant_name']].append(task)
    context.application.bot_data['admin_pending_tasks'] = {task['id']: task for task in pending_tasks}
    text_parts = ["<b>Кандидаты на рассмотрении:</b>"]
    buttons = []
    for restaurant_name, candidates in sorted(candidates_by_restaurant.items()):
        text_parts.append(f"\n<b>📍 {html.escape(restaurant_name)}:</b>")
        for candidate in candidates:
            button = InlineKeyboardButton(f"👤 {candidate['name']}", callback_data=f"cand_act_{candidate['id']}")
            buttons.append([button])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data=settings.CALLBACK_ADMIN_BACK)])
    await edit_admin_message(query, "\n".join(text_parts), InlineKeyboardMarkup(buttons))
    return AdminState.AWAIT_CANDIDATE_ACTION


async def handle_candidate_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    feedback_id = query.data.replace("cand_act_", "")
    all_tasks = context.application.bot_data.get('admin_pending_tasks', {})
    task = all_tasks.get(feedback_id)
    if not task:
        await query.answer("Задача этого кандидата уже неактуальна.", show_alert=True)
        return await admin_list_pending_candidates(update, context)
    candidate_name = task.get('name', 'кандидата')
    text = f"Действия для кандидата:\n<b>{html.escape(candidate_name)}</b>"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Оставить ОС", callback_data=f"fb_{feedback_id}")],
        [InlineKeyboardButton("❌ Удалить", callback_data=f"cand_del_{feedback_id}")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="admin_pending_candidates")]
    ])
    await edit_admin_message(query, text, keyboard)
    return AdminState.AWAIT_CANDIDATE_ACTION


async def handle_admin_delete_candidate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    feedback_id = query.data.replace("cand_del_", "")
    all_tasks = context.application.bot_data.get('admin_pending_tasks', {})
    task = all_tasks.get(feedback_id)
    if not task:
        await query.answer("Кандидат не найден.", show_alert=True)
        return AdminState.AWAIT_CANDIDATE_ACTION
    candidate_id = task['candidate_id']
    candidate_name = task['name']
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"cand_del_confirm_{candidate_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_pending_candidates")]])
    await edit_admin_message(query, f"Удалить анкету <b>{html.escape(candidate_name)}</b>?", keyboard)
    return AdminState.AWAIT_CANDIDATE_ACTION


async def handle_admin_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    candidate_id = int(query.data.replace("cand_del_confirm_", ""))
    await database.move_pending_feedback_to_history(candidate_id, query.from_user.id, "Удалено администратором")
    logger.info(f"Admin {query.from_user.id} deleted feedback for candidate {candidate_id}.")
    await query.answer("Кандидат удален и перемещен в архив.", show_alert=True)
    return await admin_list_pending_candidates(update, context)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    stats_by_res = await database.get_survey_counts_by_restaurant()

    if not stats_by_res:
        await query.answer("Статистика пока пуста.", show_alert=True)
        return await admin_panel_start(update, context)

    await safe_answer_callback_query(query)
    await edit_admin_message(query, "Загрузка статистики...", None)

    res_map = {code.split('_')[-1]: name for name, code in RESTAURANT_OPTIONS}; res_map['N/A'] = "Не указан"
    survey_names = {'recruitment': 'Анкеты', 'onboarding': 'Онбординг', 'manager_feedback': 'ОС менеджера',
                    'candidate_feedback': 'ОС кандидата', 'exit': 'Exit-интервью', 'climate': 'Замер климата'}
    report = ["<b>📈 Статистика по опросам:</b>\n"]; totals = defaultdict(int)
    for res_code in sorted(stats_by_res.keys(), key=lambda x: res_map.get(x, x)):
        report.append(f"\n<b>📍 {html.escape(res_map.get(res_code, res_code))}:</b>")
        for key, count in stats_by_res[res_code].items():
            if count > 0:
                report.append(f"  - {survey_names.get(key, key)}: <b>{count}</b>"); totals[key] += count
    report.append("\n\n<b>📊 Итого:</b>")
    for key, total in sorted(totals.items()): report.append(f"  - {survey_names.get(key, key)}: <b>{total}</b>")
    await edit_admin_message(query, "\n".join(report), InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=settings.CALLBACK_ADMIN_BACK)]]))
    return AdminState.MENU


async def broadcast_climate_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    if context.bot_data.get('broadcast_in_progress', False):
        await query.answer("❗️ Рассылка уже запущена.", show_alert=True)
        return await admin_panel_start(update, context)
    active_ids = await database.get_active_employees()
    if not active_ids:
        await query.answer("Не найдено активных сотрудников для рассылки.", show_alert=True)
        return await admin_panel_start(update, context)
    await safe_answer_callback_query(query)
    context.user_data['broadcast_list'] = active_ids
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Да, запустить ({len(active_ids)} чел.)", callback_data="admin_broadcast_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_broadcast_cancel")]])
    await edit_admin_message(query, f"Запустить опрос для <b>{len(active_ids)}</b> активных сотрудников?", keyboard)
    return AdminState.BROADCAST_CONFIRM


async def handle_broadcast_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AdminState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    if query.data == "admin_broadcast_cancel":
        await edit_admin_message(query, "Рассылка отменена.", None); await asyncio.sleep(1)
        return await admin_panel_start(update, context)
    context.bot_data['broadcast_in_progress'] = True
    users = context.user_data.get('broadcast_list', [])
    await edit_admin_message(query, f"Начинаю рассылку для {len(users)} сотрудников...", None)
    success, fail = 0, 0
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Пройти опрос", callback_data=settings.CALLBACK_START_CLIMATE)]])
    text = "Привет! 👋 Предлагаем пройти анонимный опрос, чтобы мы лучше понимали климат в команде."
    for user_id in users:
        try:
            await context.bot.send_message(user_id, text, reply_markup=keyboard); success += 1
            await asyncio.sleep(0.1)
        except (Forbidden, BadRequest): fail += 1
    report = f"✅ Рассылка завершена.\nУспешно: {success}, Неуспешно: {fail}"
    await context.bot.send_message(update.effective_chat.id, report)
    logger.info(f"Climate survey broadcast finished. Success: {success}, Failed: {fail}")
    context.bot_data['broadcast_in_progress'] = False
    context.user_data.clear()
    return await admin_panel_start(update, context)


async def get_invite_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id not in settings.ADMIN_IDS:
        return

    bot_username = (await context.bot.get_me()).username
    message_parts = ["<b>🔗 Ссылки для приглашения кандидатов:</b>\n"]

    for name, res_code in RESTAURANT_OPTIONS:
        code_suffix = res_code.split('_')[-1]
        onboard_link = f"https://t.me/{bot_username}?start=onboard_{code_suffix}"
        interview_link = f"https://t.me/{bot_username}?start=interview_{code_suffix}"
        message_parts.append(f"<b>📍 {html.escape(name)}</b>")
        message_parts.append(f"  - 📋 Анкета: <code>{interview_link}</code>")
        message_parts.append(f"  - ✨ Онбординг: <code>{onboard_link}</code>")

    await update.message.reply_text("\n".join(message_parts), parse_mode=ParseMode.HTML)