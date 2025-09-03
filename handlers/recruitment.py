import html
import logging
import re
import uuid
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
from telegram.error import BadRequest, Forbidden

from models import RecruitmentState
from core import settings, database, stickers
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_or_edit_message,
    send_transient_message,
    add_to_sheets_queue,
    add_user_to_interacted,
    get_now,
    format_user_for_sheets
)
from utils.keyboards import (
    RECRUITMENT_POSITION_OPTIONS,
    RESTAURANT_OPTIONS,
    YES_NO_OPTIONS,
    MARITAL_STATUS_OPTIONS,
    CHILDREN_OPTIONS,
    HEALTH_OPTIONS,
    ATTITUDE_TO_APPEARANCE_OPTIONS,
    EDUCATION_FORM_OPTIONS,
    COURSE_OPTIONS,
    EXPERIENCE_OPTIONS,
    INCOME_OPTIONS,
    JOBS_COUNT_OPTIONS,
    VACANCY_SOURCE_OPTIONS,
    build_inline_keyboard
)
from handlers.common import cancel, prompt_to_use_button
from handlers.feedback import schedule_candidate_feedback

logger = logging.getLogger(__name__)

TOTAL_QUESTIONS = 34
MIN_ANSWER_LENGTH = 10

WEEKLY_SHIFTS_OPTIONS = [
    ("1-2", "shifts_1_2"), ("2-3", "shifts_2_3"),
    ("3-4", "shifts_3_4"), ("4-5", "shifts_4_5"),
    ("5+", "shifts_5_plus")
]


def get_progress_bar(current_q: int, total_q: int) -> str:
    progress = int((current_q / total_q) * 10)
    filled = '█' * progress
    empty = '░' * (10 - progress)
    percent = int((current_q / total_q) * 100)
    return f"[{filled}{empty}] {percent}%"


def get_question_header(context: ContextTypes.DEFAULT_TYPE) -> str:
    current_q = context.user_data.setdefault('current_question_num', 1)
    progress_bar = get_progress_bar(current_q, TOTAL_QUESTIONS)
    return f"<b>Вопрос {current_q}/{TOTAL_QUESTIONS}</b>\n<code>{progress_bar}</code>\n\n"


async def start_recruitment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    user = update.effective_user
    logger.info(f"User {user.id} starting RECRUITMENT flow with deeplink.")
    await add_user_to_interacted(user.id, context)
    context.user_data.clear()
    context.user_data['conversations'] = {'recruitment_conv': True} # Флаг для /start

    if update.message:
        try:
            await update.message.delete()
        except (BadRequest, Forbidden):
            pass

    param = context.args[0] if context.args else ""
    if not param or not param.startswith("interview_"):
        await context.bot.send_message(
            update.effective_chat.id,
            "Ой, кажется, в ссылке или QR-коде ошибка. Пожалуйста, обратись к менеджеру, чтобы получить корректную ссылку."
        )
        return ConversationHandler.END

    restaurant_code_suffix = param.replace("interview_", "")
    restaurant_code = f"res_{restaurant_code_suffix}"
    restaurant_name = next((name for name, code in RESTAURANT_OPTIONS if code == restaurant_code), None)

    if not restaurant_name:
        await context.bot.send_message(
            update.effective_chat.id,
            "Не смогли определить ресторан по этой ссылке. Пожалуйста, обратись к менеджеру за помощью."
        )
        return ConversationHandler.END

    context.user_data['preselected_restaurant_code'] = restaurant_code_suffix
    context.user_data['preselected_restaurant_name'] = restaurant_name
    context.user_data['chat_id'] = update.effective_chat.id
    context.user_data['current_question_num'] = 1

    await context.bot.send_sticker(chat_id=user.id, sticker=stickers.get_random_greeting())
    await asyncio.sleep(0.5)

    header = get_question_header(context)
    text = (
        "Ciao! 👋\n\n"
        f"Рады, что ты хочешь стать частью команды «Марчеллис» в ресторане по адресу: <b>{html.escape(restaurant_name)}</b>.\n\n"
        f"Чтобы мы могли познакомиться поближе, я задам {TOTAL_QUESTIONS} вопросов. Это займет около 10-15 минут.\n\n"
        "<i>Продолжая диалог, ты даешь согласие на обработку своих персональных данных. Мы гарантируем их конфиденциальность.</i>\n\n"
        f"{header}"
        "Начнём? Напиши, пожалуйста, свои Фамилию, Имя и Отчество полностью."
    )

    sent_message = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data[settings.ACTIVE_MESSAGE_ID_KEY] = sent_message.message_id

    return RecruitmentState.FULL_NAME


async def full_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 1)
    full_name = update.message.text.strip()
    if len(full_name.split()) < 2:
        await send_transient_message(context, update.effective_chat.id,
                                     "Пожалуйста, введите как минимум Фамилию и Имя.")
        return RecruitmentState.FULL_NAME

    context.user_data['full_name'] = full_name
    first_name = full_name.split()[1] if len(full_name.split()) > 1 else full_name.split()[0]
    context.user_data['first_name'] = first_name

    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = (f"Отлично, {html.escape(first_name)}, приятно познакомиться! ✨\n\n"
            f"{header}"
            'Сколько тебе полных лет?')

    await send_or_edit_message(update, context, text)

    return RecruitmentState.AGE


async def age_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | RecruitmentState:
    context.user_data.setdefault('current_question_num', 2)
    age_text = update.message.text.strip()
    if not age_text.isdigit():
        await send_transient_message(context, update.effective_chat.id, "Пожалуйста, введите свой возраст цифрами.")
        await update.message.delete()
        return RecruitmentState.AGE

    age = int(age_text)

    if age < 16:
        await send_or_edit_message(update, context,
                                   "К сожалению, мы можем принять на работу только с 16 лет. Спасибо за твой интерес, будем рады видеть тебя в будущем! 🙏"
                                   )
        context.user_data.clear()
        return ConversationHandler.END

    if age > 100:
        await send_transient_message(context, update.effective_chat.id,
                                     "Введен маловероятный возраст. Пожалуйста, проверь и введи корректное число.")
        await update.message.delete()
        return RecruitmentState.AGE

    context.user_data['age'] = age_text
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Принято!\n\n{header}" \
           'Расскажи немного о своей семье (с кем ты живешь, кто твои близкие).'
    await send_or_edit_message(update, context, text)
    return RecruitmentState.FAMILY_INFO


async def family_info_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 3)
    answer = update.message.text.strip()
    if len(answer) < MIN_ANSWER_LENGTH:
        await send_transient_message(context, update.effective_chat.id,
                                     f"Пожалуйста, дай более развернутый ответ (хотя бы {MIN_ANSWER_LENGTH} символов).")
        return RecruitmentState.FAMILY_INFO

    context.user_data['family_info'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(VACANCY_SOURCE_OPTIONS, columns=2)
    text = f"Спасибо, что поделился(ась)!\n\n{header}" \
           'Откуда ты узнал(а) о вакансии?'
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.VACANCY_SOURCE


async def vacancy_source_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 4)
    query = update.callback_query
    await safe_answer_callback_query(query)
    source = next((name for name, data in VACANCY_SOURCE_OPTIONS if data == query.data), "Другое")
    context.user_data['vacancy_source'] = source
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    context.user_data.setdefault('selected_vacancies', [])
    buttons = [InlineKeyboardButton(f"{'✅ ' if code in context.user_data['selected_vacancies'] else ''}{name}",
                                    callback_data=code) for name, code in RECRUITMENT_POSITION_OPTIONS]
    keyboard_layout = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard_layout.append([InlineKeyboardButton("✔️ Готово", callback_data="done_vacancies")])
    text = f"{header}Какая вакансия тебя интересует? (можно выбрать несколько)"
    await send_or_edit_message(update, context, text, InlineKeyboardMarkup(keyboard_layout))
    return RecruitmentState.AWAIT_MULTI_VACANCY


async def multi_vacancy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 5)
    query = update.callback_query
    await safe_answer_callback_query(query)
    action = query.data
    selected_vacancies = context.user_data.setdefault('selected_vacancies', [])

    if action == "done_vacancies":
        if not selected_vacancies:
            await query.answer("Пожалуйста, выбери хотя бы одну вакансию.", show_alert=True)
            return RecruitmentState.AWAIT_MULTI_VACANCY

        if 'vac_Other' in selected_vacancies:
            await send_or_edit_message(update, context,
                                       "Ты выбрал(а) 'Другое'. Пожалуйста, уточни, какая должность тебя интересует?")
            return RecruitmentState.AWAIT_OTHER_VACANCY

        context.user_data['applied_position'] = ", ".join(
            [next(name for name, code in RECRUITMENT_POSITION_OPTIONS if code == v_code) for v_code in
             selected_vacancies])
        context.user_data['current_question_num'] += 1
        header = get_question_header(context)
        text = f"Выбрано: <b>{html.escape(context.user_data['applied_position'])}</b>.\n\n{header}Почему именно эта работа/вакансия тебя привлекает?"
        await send_or_edit_message(update, context, text)
        return RecruitmentState.REASON_FOR_CHOICE

    if action in selected_vacancies:
        selected_vacancies.remove(action)
    else:
        selected_vacancies.append(action)

    buttons = [InlineKeyboardButton(f"{'✅ ' if code in selected_vacancies else ''}{name}", callback_data=code) for
               name, code in RECRUITMENT_POSITION_OPTIONS]
    keyboard_layout = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard_layout.append([InlineKeyboardButton("✔️ Готово", callback_data="done_vacancies")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard_layout))
    return RecruitmentState.AWAIT_MULTI_VACANCY


async def other_vacancy_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    other_position = update.message.text.strip()
    selected_vacancies = context.user_data.get('selected_vacancies', [])

    # Формируем итоговый список должностей
    final_positions = []
    for code in selected_vacancies:
        if code == 'vac_Other':
            final_positions.append(other_position)
        else:
            final_positions.append(next(name for name, c in RECRUITMENT_POSITION_OPTIONS if c == code))

    context.user_data['applied_position'] = ", ".join(final_positions)
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Выбрано: <b>{html.escape(context.user_data['applied_position'])}</b>.\n\n{header}Почему именно эта работа/вакансия тебя привлекает?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.REASON_FOR_CHOICE


async def reason_for_choice_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 6)
    answer = update.message.text.strip()
    if len(answer) < MIN_ANSWER_LENGTH:
        await send_transient_message(context, update.effective_chat.id,
                                     f"Пожалуйста, дай более развернутый ответ (хотя бы {MIN_ANSWER_LENGTH} символов).")
        return RecruitmentState.REASON_FOR_CHOICE

    context.user_data['reason_for_choice'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    context.user_data.setdefault('preferred_restaurants', [])
    buttons = [
        InlineKeyboardButton(f"{'✅ ' if code in context.user_data.get('preferred_restaurant_codes', []) else ''}{name}",
                             callback_data=code) for name, code in RESTAURANT_OPTIONS]
    keyboard_layout = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard_layout.append([InlineKeyboardButton("✔️ Готово", callback_data="done_restaurants")])
    text = f"Интересный выбор! Спасибо.\n\n{header}" \
           'В каком из наших ресторанов ты хотел(а) бы работать? (можно выбрать несколько)'
    await send_or_edit_message(update, context, text, InlineKeyboardMarkup(keyboard_layout))
    return RecruitmentState.AWAIT_MULTI_RESTAURANT


async def multi_restaurant_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 7)
    query = update.callback_query
    await safe_answer_callback_query(query)
    action = query.data
    selected_restaurants_codes = context.user_data.setdefault('preferred_restaurant_codes', [])

    if action == "done_restaurants":
        if not selected_restaurants_codes:
            await query.answer("Пожалуйста, выбери хотя бы один ресторан.", show_alert=True)
            return RecruitmentState.AWAIT_MULTI_RESTAURANT

        context.user_data['preferred_restaurant'] = ", ".join(
            [next(name for name, code in RESTAURANT_OPTIONS if code == r_code) for r_code in
             selected_restaurants_codes])
        context.user_data['current_question_num'] += 1
        header = get_question_header(context)
        keyboard = build_inline_keyboard(YES_NO_OPTIONS, columns=2)
        text = f"{header}Ты уже знаком(а) с ресторанами «Марчеллис» как гость?"
        await send_or_edit_message(update, context, text, keyboard)
        return RecruitmentState.KNOWS_MARCELLIS

    if action in selected_restaurants_codes:
        selected_restaurants_codes.remove(action)
    else:
        selected_restaurants_codes.append(action)

    buttons = [InlineKeyboardButton(f"{'✅ ' if code in selected_restaurants_codes else ''}{name}", callback_data=code)
               for name, code in RESTAURANT_OPTIONS]
    keyboard_layout = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard_layout.append([InlineKeyboardButton("✔️ Готово", callback_data="done_restaurants")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard_layout))
    return RecruitmentState.AWAIT_MULTI_RESTAURANT


async def knows_marcellis_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 8)
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data['knows_marcellis'] = "Да" if query.data == "yes" else "Нет"
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(YES_NO_OPTIONS, columns=2)
    text = f"{header}🌙 Готов(а) ли ты к ночным сменам?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.NIGHT_SHIFTS


async def night_shifts_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 9)
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data['night_shifts'] = "Да" if query.data == "yes" else "Нет"
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(WEEKLY_SHIFTS_OPTIONS, columns=3)
    text = f"{header}🗓️ Сколько полных смен в неделю ты готов(а) нам уделять? (если полная смена = 12 часов)"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.WEEKLY_SHIFTS


async def weekly_shifts_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 10)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((name for name, data in WEEKLY_SHIFTS_OPTIONS if data == query.data), "Не указано")
    context.user_data['weekly_shifts'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"{header}📱 Оставь, пожалуйста, свой мобильный телефон для связи (например, +79991234567):"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.MOBILE_PHONE


async def mobile_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 11)
    phone_text = update.message.text.strip()
    cleaned_phone = re.sub(r'\D', '', phone_text)
    if len(cleaned_phone) == 11 and cleaned_phone.startswith(('7', '8')):
        cleaned_phone = cleaned_phone[1:]
    if len(cleaned_phone) != 10:
        await send_transient_message(context, update.effective_chat.id,
                                     "Пожалуйста, введи корректный 10-значный номер телефона (например, +7 999 123-45-67).")
        await update.message.delete()
        return RecruitmentState.MOBILE_PHONE
    context.user_data['mobile_phone'] = f"+7{cleaned_phone}"
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Записал!\n\n{header}🌐 Есть ссылка на твой профиль в соцсетях (VK, Instagram и т.д.)? Если нет, просто напиши «нет»."
    await send_or_edit_message(update, context, text)
    return RecruitmentState.SOCIAL_LINK


async def social_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 12)
    context.user_data['social_link'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Окей!\n\n{header}🏙️ В каком городе ты родился(ась)?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.CITY


async def city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 13)
    context.user_data['city'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Понятно!\n\n{header}🏠 А где ты сейчас живешь (город, район, ближайшее метро)?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.ADDRESS


async def address_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 14)
    context.user_data['address'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(MARITAL_STATUS_OPTIONS, columns=2)
    text = f"Принято, двигаемся дальше.\n\n{header}Каково твое семейное положение?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.MARITAL_STATUS


async def marital_status_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 15)
    query = update.callback_query
    await safe_answer_callback_query(query)
    status = next((name for name, data in MARITAL_STATUS_OPTIONS if data == query.data), "Не указано")
    context.user_data['marital_status'] = status
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(CHILDREN_OPTIONS, columns=4)
    text = f"{header}👶 Есть ли у тебя дети?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.CHILDREN


async def children_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 16)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((text for text, data in CHILDREN_OPTIONS if data == query.data), "Не указано")
    context.user_data['children'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(HEALTH_OPTIONS, columns=2)
    text = f"{header}💪 Как ты оцениваешь уровень своего здоровья?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.HEALTH_ASSESSMENT


async def health_assessment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 17)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((text for text, data in HEALTH_OPTIONS if data == query.data), "Не указано")
    context.user_data['health_assessment'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(ATTITUDE_TO_APPEARANCE_OPTIONS, columns=1)
    text = f"{header}✨ Как ты относишься к своей внешности?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.ATTITUDE_TO_APPEARANCE


async def attitude_to_appearance_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 18)
    query = update.callback_query
    await safe_answer_callback_query(query)
    attitude = next((name for name, data in ATTITUDE_TO_APPEARANCE_OPTIONS if data == query.data), "Не указано")
    context.user_data['attitude_to_appearance'] = attitude
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"{header}🎓 Напиши название твоего последнего учебного заведения (полностью):"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.EDUCATION_NAME


async def education_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 19)
    context.user_data['education_name'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Супер!\n\n{header}📅 Год поступления / год окончания:"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.GRADUATION_YEAR


async def graduation_year_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 20)
    context.user_data['graduation_year'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(COURSE_OPTIONS, columns=4)
    text = f"Записал.\n\n{header}🔢 На каком курсе учишься сейчас?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.COURSE


async def course_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 21)
    query = update.callback_query
    await safe_answer_callback_query(query)
    course = next((name for name, data in COURSE_OPTIONS if data == query.data), "Не указано")
    context.user_data['course'] = course
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(EDUCATION_FORM_OPTIONS, columns=2)
    text = f"Принято.\n\n{header}🏛️ Форма обучения:"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.EDUCATION_FORM


async def education_form_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 22)
    query = update.callback_query
    await safe_answer_callback_query(query)
    form = next((name for name, data in EDUCATION_FORM_OPTIONS if data == query.data), "Не указано")
    context.user_data['education_form'] = form
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Да, напишу", callback_data="courses_yes")],
        [InlineKeyboardButton("Нет", callback_data="courses_no")]
    ])
    text = f"{header}📚 Проходил(а) ли ты какие-либо курсы, тренинги, семинары?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.AWAIT_ADDITIONAL_COURSES_DECISION


async def additional_courses_decision_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 23)
    query = update.callback_query
    await safe_answer_callback_query(query)

    if query.data == "courses_yes":
        await send_or_edit_message(update, context, "Пожалуйста, перечисли их:")
        return RecruitmentState.ADDITIONAL_COURSES
    else:
        context.user_data['additional_courses'] = "Нет"
        context.user_data['current_question_num'] += 1
        header = get_question_header(context)
        keyboard = build_inline_keyboard(EXPERIENCE_OPTIONS, columns=2)
        text = f"Принято!\n\n{header}👷‍♂️ Есть ли у тебя опыт работы на той позиции, на которую претендуешь?"
        await send_or_edit_message(update, context, text, keyboard)
        return RecruitmentState.EXPERIENCE_DURATION


async def additional_courses_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 23)
    context.user_data['additional_courses'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(EXPERIENCE_OPTIONS, columns=2)
    text = f"Принято!\n\n{header}👷‍♂️ Есть ли у тебя опыт работы на той позиции, на которую претендуешь?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.EXPERIENCE_DURATION


async def experience_duration_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 24)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((text for text, data in EXPERIENCE_OPTIONS if data == query.data), "Не указано")
    context.user_data['experience_duration'] = answer
    context.user_data['current_question_num'] += 1

    if query.data == "exp_0":
        context.user_data['experience_details'] = "Нет"
        context.user_data['current_question_num'] += 1
        header = get_question_header(context)
        keyboard = build_inline_keyboard(INCOME_OPTIONS, columns=2)
        text = f"{header}💰 На какой доход в месяц ты рассчитываешь?"
        await send_or_edit_message(update, context, text, keyboard)
        return RecruitmentState.EXPECTED_INCOME
    else:
        header = get_question_header(context)
        text = f"{header}🏢 Опиши свой последний опыт: название компании, даты работы, должность и обязанности."
        await send_or_edit_message(update, context, text)
        return RecruitmentState.AWAIT_EXPERIENCE_DETAILS


async def experience_details_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 25)
    context.user_data['experience_details'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(INCOME_OPTIONS, columns=2)
    text = f"Подробно! Спасибо.\n\n{header}💰 На какой доход в месяц ты рассчитываешь?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.EXPECTED_INCOME


async def expected_income_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 26)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((text for text, data in INCOME_OPTIONS if data == query.data), "Не указано")
    context.user_data['expected_income'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"{header}🚶‍♂️ Какова была настоящая причина ухода с последнего места работы?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.REASON_FOR_LEAVING


async def reason_for_leaving_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 27)
    answer = update.message.text.strip()
    if len(answer) < MIN_ANSWER_LENGTH:
        await send_transient_message(context, update.effective_chat.id,
                                     f"Пожалуйста, дай более развернутый ответ (хотя бы {MIN_ANSWER_LENGTH} символов).")
        return RecruitmentState.REASON_FOR_LEAVING
    context.user_data['reason_for_leaving'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(JOBS_COUNT_OPTIONS, columns=3)
    text = f"Спасибо за честность.\n\n{header}🏢 Укажи общее количество компаний, где ты когда-либо работал(а):"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.PREVIOUS_JOBS_COUNT


async def previous_jobs_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 28)
    query = update.callback_query
    await safe_answer_callback_query(query)
    answer = next((text for text, data in JOBS_COUNT_OPTIONS if data == query.data), "Не указано")
    context.user_data['previous_jobs_count'] = answer
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Отлично! Осталось совсем немного вопросов о тебе как о личности.\n\n{header}🤸‍♂️ Расскажи о своем отношении к спорту (занимаешься ли, чем увлекаешься):"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.ATTITUDE_TO_SPORT


async def attitude_to_sport_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 29)
    context.user_data['attitude_to_sport'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Здорово!\n\n{header}✨ Перечисли три самых ценных для тебя качества в руководителе."
    await send_or_edit_message(update, context, text)
    return RecruitmentState.LIFE_VALUES


async def life_values_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 30)
    context.user_data['life_values'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Интересный взгляд!\n\n{header}🪨 Назови свои главные достоинства как сотрудника."
    await send_or_edit_message(update, context, text)
    return RecruitmentState.LIFE_WEAKNESSES


async def life_weaknesses_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 31)
    context.user_data['life_weaknesses'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Спасибо!\n\n{header}🎯 В какой сфере ты хотел(а) бы реализоваться в жизни?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.LIFE_GOAL


async def life_goal_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 32)
    context.user_data['life_goal'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    text = f"Хорошая цель!\n\n{header}📖 Какую книгу ты сейчас читаешь или какой курс проходишь?"
    await send_or_edit_message(update, context, text)
    return RecruitmentState.READING_NOW


async def reading_now_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> RecruitmentState:
    context.user_data.setdefault('current_question_num', 33)
    context.user_data['reading_now'] = update.message.text.strip()
    context.user_data['current_question_num'] += 1
    header = get_question_header(context)
    keyboard = build_inline_keyboard(YES_NO_OPTIONS, columns=2)
    text = f"И последний вопрос, финишная прямая!\n\n{header}⚖️ Есть ли у тебя судимость?"
    await send_or_edit_message(update, context, text, keyboard)
    return RecruitmentState.JUDGED_BEFORE


async def judged_before_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault('current_question_num', 34)
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data['judged_before'] = "Да" if query.data == "yes" else "Нет"
    await send_or_edit_message(update, context,
                               "Отлично! Это был последний вопрос. 🚀\n\nСпасибо за уделенное время! Мы обрабатываем твою анкету...")
    await send_recruitment_results(context)

    first_name = context.user_data.get('first_name', 'кандидат')
    final_text = f"Спасибо, {html.escape(first_name)}! Твоя анкета принята. Менеджер скоро свяжется с тобой. Хорошего дня!"
    await send_or_edit_message(update, context, final_text, None)

    context.user_data.clear()
    return ConversationHandler.END


async def send_recruitment_results(context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    chat_id = user_data.get('chat_id')
    user_full_name = user_data.get('full_name', f'Кандидат_{chat_id}')
    user = await context.bot.get_chat(chat_id)

    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")
    interview_restaurant_code_suffix = user_data.get("preselected_restaurant_code")

    report_fields_for_sheets = [
        "preselected_restaurant_name", "full_name", "age", "family_info", "vacancy_source",
        "applied_position", "reason_for_choice", "preferred_restaurant", "knows_marcellis",
        "night_shifts", "weekly_shifts", "mobile_phone", "social_link", "city", "address", "marital_status",
        "children", "health_assessment", "attitude_to_appearance", "education_name",
        "graduation_year", "course", "education_form", "additional_courses",
        "experience_duration", "experience_details", "expected_income", "reason_for_leaving",
        "previous_jobs_count", "attitude_to_sport", "life_values", "life_weaknesses",
        "life_goal", "reading_now", "judged_before"
    ]

    user_link = format_user_for_sheets(chat_id, user_full_name, user.username)
    row_data = [timestamp, user_link]

    for key in report_fields_for_sheets:
        row_data.append(user_data.get(key, "N/A"))

    await add_to_sheets_queue(settings.INTERVIEW_SHEET_NAME, row_data)
    await database.log_survey_completion('recruitment', chat_id, interview_restaurant_code_suffix)
    await database.log_candidate_restaurant(chat_id, interview_restaurant_code_suffix)

    def clean_text(key, default="—"):
        text = str(user_data.get(key, default))
        text = text.replace('💍 ', '').replace('❤️ ', '').replace('🚶 ', '').replace('💔 ', '')
        text = text.replace('😍 ', '').replace('🙂 ', '').replace('😕 ', '')
        text = text.replace('☀️ ', '').replace('🌙 ', '').replace('✉️ ', '').replace('💻 ', '')
        return html.escape(text)

    full_report_parts = [
        f"<b>Кандидат:</b> <code>{clean_text('full_name')}</code>, {clean_text('age')} лет",
        f"<b>Вакансия:</b> {clean_text('applied_position')}",
        f"<b>Ресторан:</b> {clean_text('preselected_restaurant_name')}",
        f"<b>Телефон:</b> <code>{clean_text('mobile_phone')}</code>",
        f"<b>Соц. сеть:</b> <code>{clean_text('social_link')}</code>",
        "",
        "──────────────",
        "<b>КЛЮЧЕВЫЕ ДАННЫЕ</b>",
        "──────────────",
        f"<b>Ожидания по доходу:</b> {clean_text('expected_income')}",
        f"<b>Опыт:</b> {clean_text('experience_duration')}",
        f"<b>Предыдущие места работы:</b> {clean_text('previous_jobs_count')}",
        f"<b>Ночные смены:</b> {clean_text('night_shifts')}",
        f"<b>Смен в неделю:</b> {clean_text('weekly_shifts')}",
        f"<b>Знакомство с брендом:</b> {clean_text('knows_marcellis')}",
        f"<b>Судимость:</b> {clean_text('judged_before')}",
        f"<b>Состояние здоровья:</b> {clean_text('health_assessment')}",
        f"<b>Дети:</b> {clean_text('children')}",
        "",
        "──────────────",
        "<b>РАЗВЕРНУТЫЕ ОТВЕТЫ</b>",
        "──────────────",
        "",
        "🤔 <b>Мотивация и цели</b>",
        f"<i>{clean_text('reason_for_choice')}</i>",
        "",
        "🚶‍♂️ <b>Причина ухода с прошлого места</b>",
        f"<i>{clean_text('reason_for_leaving')}</i>",
        "",
        "📋 <b>Детали опыта работы</b>",
        f"<i>{clean_text('experience_details')}</i>",
        "",
        "💪 <b>Сильные стороны и ценности</b>",
        f"<i>Сильные стороны: {clean_text('life_weaknesses')}\nКачества в руководителе: {clean_text('life_values')}</i>",
        "",
        "🎓 <b>Образование</b>",
        f"<i>Учебное заведение: {clean_text('education_name')} ({clean_text('education_form')})\nГоды/Курс: {clean_text('graduation_year')} / {clean_text('course')}\nДоп. курсы: {clean_text('additional_courses')}</i>",
        "",
        "👤 <b>Личная информация</b>",
        f"<i>Проживание: {clean_text('address')}\nСемейное положение: {clean_text('marital_status')}\nСемья: {clean_text('family_info')}\nСпорт: {clean_text('attitude_to_sport')}\nКниги/Курсы: {clean_text('reading_now')}\nВнешность: {clean_text('attitude_to_appearance')}</i>",
    ]
    full_report_text = "\n".join(full_report_parts)

    summary_text = (
        f"⚡️ Новая анкета!\n\n"
        f"Кандидат: <b>{clean_text('full_name')}</b> ({clean_text('age')})\n"
        f"Позиция: <b>{clean_text('applied_position')}</b>\n"
        f"Ресторан: <b>{clean_text('preselected_restaurant_name')}</b>\n\n"
        f"💵 {clean_text('expected_income')}\n"
        f"🕒 Опыт: {clean_text('experience_duration')}\n"
        f"🌙 Ночи: {clean_text('night_shifts')}"
    )

    recipients = set(settings.ADMIN_IDS)
    if interview_restaurant_code_suffix:
        manager_ids = await database.get_managers_for_restaurant(interview_restaurant_code_suffix)
        recipients.update(manager_ids)

    job_context_for_managers = {
        "candidate_id": chat_id,
        "candidate_name": user_data.get('full_name', 'Кандидат'),
        "position": user_data.get('applied_position', '—'),
        "full_name": user_data.get('full_name', '—'),
        "address": user_data.get('address', '—'),
        "phone": user_data.get('mobile_phone', '—'),
        "interview_restaurant_code": interview_restaurant_code_suffix,
        "interview_restaurant_name": user_data.get('preselected_restaurant_name', 'Не указан'),
        "preferred_restaurant_codes": user_data.get('preferred_restaurant_codes', []),
        "recruitment_report": full_report_text
    }

    # --- Регистрация кандидата в БД как НЕАКТИВНОГО ---
    # ПРИМЕЧАНИЕ: Вам потребуется создать эту функцию в core/database.py
    # Она должна добавлять запись в таблицу employees со статусом is_active = 0
    await database.register_candidate(
        user_id=chat_id,
        full_name=user_full_name,
        restaurant_code=interview_restaurant_code_suffix
    )
    logger.info(f"Candidate {chat_id} registered in the system as inactive.")

    for recipient_id in recipients:
        try:
            feedback_id = str(uuid.uuid4())
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📄 Посмотреть полную анкету", callback_data=f"show_full_report_{feedback_id}")
            ]])

            sent_message = await context.bot.send_message(recipient_id, summary_text, parse_mode=ParseMode.HTML,
                                                          reply_markup=keyboard)

            await database.add_pending_feedback(
                feedback_id=feedback_id,
                manager_id=recipient_id,
                message_id=sent_message.message_id,
                candidate_id=chat_id,
                candidate_name=user_data.get('full_name', 'Кандидат'),
                job_data=job_context_for_managers,
                created_at=get_now().timestamp()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки анкеты и создания задачи для получателя {recipient_id}: {e}")

    if context.job_queue:
        job_context_for_candidate = {"candidate_id": chat_id}
        context.job_queue.run_once(schedule_candidate_feedback, when=settings.FEEDBACK_DELAY_SECONDS,
                                   data=job_context_for_candidate, name=f"cand_feedback_{chat_id}")

        logger.info(f"Scheduled candidate feedback for candidate {chat_id}")


async def show_full_recruitment_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback_query(query)
    feedback_id = query.data.replace("show_full_report_", "")

    # Сначала ищем в активных задачах
    feedback_task = await database.get_pending_feedback_by_id(feedback_id)
    # Если не нашли, ищем в истории
    if not feedback_task:
        feedback_task = await database.get_feedback_from_history(feedback_id)

    if not feedback_task:
        await query.edit_message_text(
            f"{query.message.text}\n\n<i>(Анкета не найдена в активных задачах или истории.)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        return

    full_report = feedback_task.get("job_data", {}).get("recruitment_report", "Не удалось загрузить полную анкету.")

    try:
        await query.edit_message_text(
            text=full_report,
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error editing message to full report: {e}")
            await query.message.reply_text(text=full_report, parse_mode=ParseMode.HTML)


async def send_candidate_check_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback_query(query)
    candidate_id = int(query.data.replace("check_candidate_", ""))
    check_data = context.bot_data.get('candidate_check_info', {}).get(candidate_id)

    if not check_data:
        await query.edit_message_text("Данные для проверки не найдены (возможно, они устарели).")
        return

    check_message_parts = [
        f"<b>1.</b> {html.escape(check_data['position'])}",
        f"<b>2.</b> <code>{html.escape(check_data['full_name'])}</code>",
        "<b>3. В проекте не работал</b>",
        f"<b>4.</b> {html.escape(check_data['address'])}",
        f"<b>5.</b> <code>{html.escape(check_data['phone'])}</code>",
    ]
    check_message = "\n".join(check_message_parts)
    await query.message.reply_text(text=check_message, parse_mode=ParseMode.HTML)
    await query.edit_message_reply_markup(reply_markup=None)


recruitment_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_recruitment_flow, filters=filters.Regex(r'interview_'))],
    states={
        RecruitmentState.FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, full_name_received)],
        RecruitmentState.AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, age_received)],
        RecruitmentState.FAMILY_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, family_info_received)],
        RecruitmentState.VACANCY_SOURCE: [CallbackQueryHandler(vacancy_source_received, pattern="^src_"),
                                          MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.AWAIT_MULTI_VACANCY: [
            CallbackQueryHandler(multi_vacancy_handler, pattern="^vac_|done_vacancies$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.AWAIT_OTHER_VACANCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, other_vacancy_received)],
        RecruitmentState.REASON_FOR_CHOICE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reason_for_choice_received)],
        RecruitmentState.AWAIT_MULTI_RESTAURANT: [
            CallbackQueryHandler(multi_restaurant_handler, pattern="^res_|done_restaurants$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.KNOWS_MARCELLIS: [CallbackQueryHandler(knows_marcellis_received, pattern="^(yes|no)$"),
                                           MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.NIGHT_SHIFTS: [CallbackQueryHandler(night_shifts_received, pattern="^(yes|no)$"),
                                        MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.WEEKLY_SHIFTS: [CallbackQueryHandler(weekly_shifts_received, pattern="^shifts_"),
                                         MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.MOBILE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_phone_received)],
        RecruitmentState.SOCIAL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, social_link_received)],
        RecruitmentState.CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_received)],
        RecruitmentState.ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_received)],
        RecruitmentState.MARITAL_STATUS: [CallbackQueryHandler(marital_status_received, pattern="^m_"),
                                          MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.CHILDREN: [CallbackQueryHandler(children_received, pattern="^child_"),
                                    MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.HEALTH_ASSESSMENT: [CallbackQueryHandler(health_assessment_received, pattern="^health_"),
                                             MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.ATTITUDE_TO_APPEARANCE: [
            CallbackQueryHandler(attitude_to_appearance_received, pattern="^app_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.EDUCATION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, education_name_received)],
        RecruitmentState.GRADUATION_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, graduation_year_received)],
        RecruitmentState.COURSE: [CallbackQueryHandler(course_received, pattern="^course_"),
                                  MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.EDUCATION_FORM: [CallbackQueryHandler(education_form_received, pattern="^form_"),
                                          MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.AWAIT_ADDITIONAL_COURSES_DECISION: [
            CallbackQueryHandler(additional_courses_decision_received, pattern="^courses_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.ADDITIONAL_COURSES: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, additional_courses_received)],
        RecruitmentState.EXPERIENCE_DURATION: [CallbackQueryHandler(experience_duration_received, pattern="^exp_"),
                                               MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.AWAIT_EXPERIENCE_DETAILS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, experience_details_received)],
        RecruitmentState.EXPECTED_INCOME: [CallbackQueryHandler(expected_income_received, pattern="^inc_"),
                                           MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.REASON_FOR_LEAVING: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reason_for_leaving_received)],
        RecruitmentState.PREVIOUS_JOBS_COUNT: [CallbackQueryHandler(previous_jobs_count_received, pattern="^jobs_"),
                                               MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
        RecruitmentState.ATTITUDE_TO_SPORT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, attitude_to_sport_received)],
        RecruitmentState.LIFE_VALUES: [MessageHandler(filters.TEXT & ~filters.COMMAND, life_values_received)],
        RecruitmentState.LIFE_WEAKNESSES: [MessageHandler(filters.TEXT & ~filters.COMMAND, life_weaknesses_received)],
        RecruitmentState.LIFE_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, life_goal_received)],
        RecruitmentState.READING_NOW: [MessageHandler(filters.TEXT & ~filters.COMMAND, reading_now_received)],
        RecruitmentState.JUDGED_BEFORE: [CallbackQueryHandler(judged_before_received, pattern="^(yes|no)$"),
                                         MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="recruitment_conv",
    persistent=True,
    per_message=False,
    conversation_timeout=settings.CONVERSATION_TIMEOUT_SECONDS
)