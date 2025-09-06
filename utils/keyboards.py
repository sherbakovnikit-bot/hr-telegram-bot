import locale
from datetime import date, timedelta
from typing import List, Tuple, Dict, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from utils.helpers import build_inline_keyboard, get_now
from core import settings

try:
    locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'russian')
    except locale.Error:
        print("Warning: Could not set Russian locale. Dates might be in English.")

InlineButtonOption = Tuple[str, str]

RESTAURANT_OPTIONS: List[InlineButtonOption] = [
    ("Восстания, 15", "res_V15"), ("Одоевского, 34", "res_O34"), ("Типанова, 27/39", "res_T27"),
    ("Московский, 205", "res_M205"), ("Ленинский, 120", "res_L120"), ("Невский, 21", "res_N21"),
    ("Рубинштейна, 1/43", "res_R1"), ("Грибоедова 18-20", "res_G18"), ("Науки, 14А", "res_N14"),
    ("Энгельса, 124", "res_E124"), ("МСК, Камергерский", "res_MSK"), ("Мурино", "res_MUR")
]

ONBOARDING_POSITION_OPTIONS: List[InlineButtonOption] = [
    ("Хостес", "onboard_pos_Hostess"), ("Официант", "onboard_pos_Waiter"),
    ("Бармен", "onboard_pos_Bartender"), ("Другое", "onboard_pos_Other")
]

EXIT_POSITION_OPTIONS: List[InlineButtonOption] = [
    ("Официант", "exit_pos_Waiter"), ("Бармен", "exit_pos_Bartender"), ("Повар", "exit_pos_Cook"),
    ("Хостес", "exit_pos_Hostess"), ("Менеджер", "exit_pos_Manager"), ("Бригадир", "exit_pos_Brigadier"),
    ("Кассир", "exit_pos_Cashier"), ("Мойка/Уборка", "exit_pos_Cleaner"), ("Курьер", "exit_pos_Courier"),
    ("Менеджер доставки", "exit_pos_DeliveryManager")
]

RECRUITMENT_POSITION_OPTIONS: List[InlineButtonOption] = [
    ("Официант", "vac_Waiter"), ("Бармен", "vac_Bartender"), ("Повар", "vac_Cook"),
    ("Хостес", "vac_Hostess"), ("Менеджер", "vac_Manager"), ("Бригадир", "vac_Brigadier"),
    ("Кассир", "vac_Cashier"), ("Мойка/Уборка", "vac_Cleaner"), ("Курьер", "vac_Courier"),
    ("Менеджер доставки", "vac_DeliveryManager"), ("Другое", "vac_Other")
]

DURATION_OPTIONS: List[InlineButtonOption] = [
    ("< 1 мес", "dur_1m"), ("1-6 мес", "dur_6m"), ("6-12 мес", "dur_1y"),
    ("1-2 года", "dur_2y"), ("> 2 лет", "dur_2y+")
]

RATING_OPTIONS: List[InlineButtonOption] = [
    ("1 (Очень плохо)", "rate_1"), ("2 (Плохо)", "rate_2"), ("3 (Нормально)", "rate_3"),
    ("4 (Хорошо)", "rate_4"), ("5 (Отлично!)", "rate_5")
]

TRAINING_OPTIONS: List[InlineButtonOption] = [
    ("Да, полностью", "train_Full"), ("В основном да", "train_Mostly"),
    ("Частично", "train_Partly"), ("Нет, недостаточно", "train_No")
]

FEEDBACK_OPTIONS: List[InlineButtonOption] = [
    ("Регулярно", "feed_Regular"), ("Иногда", "feed_Sometimes"),
    ("Редко", "feed_Rarely"), ("Никогда", "feed_Never")
]

GENDER_OPTIONS: List[InlineButtonOption] = [("Мужчина 👨", "climate_gender_male"),
                                            ("Женщина 👩", "climate_gender_female")]

YES_NO_OPTIONS: List[InlineButtonOption] = [("Да", "yes"), ("Нет", "no")]
YES_NO_OPTIONS_CLIMATE: List[InlineButtonOption] = [("Да 👍", "climate_yes"), ("Нет 👎", "climate_no")]

YES_NO_MAYBE_OPTIONS: List[InlineButtonOption] = [
    ("✅ Да", "climate_q_yes"), ("☑️ Скорее да", "climate_q_mostly_yes"),
    ("❌ Скорее нет", "climate_q_mostly_no"), ("🚫 Нет", "climate_q_no")
]

CHILDREN_OPTIONS: List[InlineButtonOption] = [
    ("Нет", "child_0"), ("1", "child_1"), ("2", "child_2"), ("Больше 2", "child_many")
]

HEALTH_OPTIONS: List[InlineButtonOption] = [
    ("Отличное", "health_excellent"), ("Хорошее", "health_good"),
    ("Нормальное", "health_normal"), ("Плохое", "health_bad")
]

EXPERIENCE_OPTIONS: List[InlineButtonOption] = [
    ("Нет опыта", "exp_0"), ("До 3 мес.", "exp_3m"), ("3-6 мес.", "exp_6m"),
    ("6-12 мес.", "exp_1y"), ("1-3 года", "exp_3y"), ("Более 3 лет", "exp_many")
]

INCOME_OPTIONS: List[InlineButtonOption] = [
    ("до 60 000 ₽", "inc_60"), ("до 80 000 ₽", "inc_80"), ("до 100 000 ₽", "inc_100"),
    ("до 120 000 ₽", "inc_120"), ("Выше 120 000 ₽", "inc_more")
]

JOBS_COUNT_OPTIONS: List[InlineButtonOption] = [
    ("1", "jobs_1"), ("2", "jobs_2"), ("3", "jobs_3"), ("4", "jobs_4"), ("Более 4", "jobs_many")
]

COURSE_OPTIONS: List[InlineButtonOption] = [
    ("1", "course_1"), ("2", "course_2"), ("3", "course_3"), ("4", "course_4"),
    ("5", "course_5"), ("6", "course_6"), ("Не учусь", "course_none")
]

MARITAL_STATUS_OPTIONS: List[InlineButtonOption] = [
    ("💍 Женат/Замужем", "m_married"), ("❤️ В отношениях", "m_relation"),
    ("🚶 Свободен(на)", "m_single"), ("💔 Разведен(а)", "m_divorced")
]

ATTITUDE_TO_APPEARANCE_OPTIONS: List[InlineButtonOption] = [
    ("😍 Считаю себя красивым(ой)", "app_nice"), ("🙂 У меня обычная внешность", "app_normal"),
    ("😕 Считаю себя непривлекательным(ой)", "app_not_nice")
]

EDUCATION_FORM_OPTIONS: List[InlineButtonOption] = [
    ("☀️ Очная", "form_full"), ("🌙 Вечерняя", "form_evening"),
    ("✉️ Заочная", "form_part"), ("💻 Дистанционная", "form_remote")
]

VACANCY_SOURCE_OPTIONS: List[InlineButtonOption] = [
    ("🌐 HeadHunter", "src_hh"), ("✈️ Telegram", "src_tg"),
    ("🗣️ Посоветовали", "src_ref"), ("📝 Авито", "src_avito"),
    ("🚶 С улицы", "src_walk"), ("❓ Другое", "src_other")
]

MANAGER_FEEDBACK_OPTIONS: List[InlineButtonOption] = [
    ("✅ Ознакомительная смена", f"{settings.CALLBACK_MGR_FEEDBACK_PREFIX}onboarding"),
    ("🤔 Подумает", f"{settings.CALLBACK_MGR_FEEDBACK_PREFIX}thinking"),
    ("❌ Отказался", f"{settings.CALLBACK_MGR_FEEDBACK_PREFIX}refused"),
    ("⛔️ Не подходит", f"{settings.CALLBACK_MGR_FEEDBACK_PREFIX}unsuitable"),
]

CANDIDATE_FEEDBACK_RATING_OPTIONS: List[InlineButtonOption] = [
    ("😞 1", "cand_rate_1"), ("😕 2", "cand_rate_2"), ("😐 3", "cand_rate_3"),
    ("🙂 4", "cand_rate_4"), ("😀 5", "cand_rate_5"),
]

INTEREST_RATING_OPTIONS: List[InlineButtonOption] = [
    (str(i), f"onboard_rate_{i}") for i in range(1, 11)
]

POSITION_LINKS: Dict[str, List[Dict[str, str]]] = {
    "Официант": [
        {"name": "Подпишись на канал, чтобы быть в курсе событий", "url": "https://t.me/+1c-IyuONGdk0NDEy", "is_critical": True},
        {"name": "Меню и словарь ингредиентов", "url": "https://docs.google.com/spreadsheets/d/1wb5RszKAdkb9zRgvVijHYrve6K63x5KljCZO_9MokHw/edit?gid=176953636", "is_critical": True},
        {"name": "Стандарты сервиса и работы официанта (для аттестации)", "url": "https://docs.google.com/spreadsheets/d/12jxORpWm9zfcA8AN84uGwV1XNJZRQPwX0TcffgabdNs/edit?usp=sharing", "is_critical": True},
        {"name": "Методичка, которая поможет тебе на адаптации", "url": "https://drive.google.com/drive/folders/1dA8QOrcng94GBigEjtkmw6celBsXq-cM?usp=share_link", "is_critical": False},
        {"name": "Дополнительные файлы, которые помогут освоиться", "url": "https://drive.google.com/drive/folders/1gcf7Io0hcVdwJuH-KkKXH_5OaUZBrGMD?usp=share_link", "is_critical": False},
        {"additional_message": "Напиши сегодня пожелания по графику своему менеджеру!"},
    ],
    "Хостес": [
        {"name": "Подпишись на канал, чтобы быть в курсе событий", "url": "https://t.me/+1c-IyuONGdk0NDEy", "is_critical": True},
        {"name": "Стандарты работы хостес (для аттестации)", "url": "https://docs.google.com/spreadsheets/d/11tJyxLR9uYTUXpfvqQ9Nvbnj6TQ7m9mk-_EkN2itt7g/edit", "is_critical": True},
        {"name": "Дополнительные файлы, которые помогут освоиться", "url": "https://drive.google.com/drive/folders/15QU-fC3fOezWsKNbRRKlWvooNYm-7P1O?usp=share_link", "is_critical": False},
        {"additional_message": "Напиши сегодня пожелания по графику своему менеджеру!"},
    ],
    "Бармен": [
        {"name": "Подпишись на канал, чтобы быть в курсе событий", "url": "https://t.me/+1c-IyuONGdk0NDEy", "is_critical": True},
        {"additional_message": "Напиши сегодня пожелания по графику старшему бармену!"},
    ],
    "Другое": [
        {"name": "Подпишись на канал, чтобы быть в курсе событий", "url": "https://t.me/+1c-IyuONGdk0NDEy", "is_critical": True},
        {"additional_message": "Желаем тебе успешного старта и увлекательной адаптации!"},
    ]
}


def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕/➖ Управление менеджерами", callback_data="admin_manage_managers")],
        [InlineKeyboardButton("👥 Управление сотрудниками", callback_data="admin_manage_employees")],
        [InlineKeyboardButton("🤔 Кандидаты на рассмотрении", callback_data="admin_pending_candidates")],
        [InlineKeyboardButton("📊 Запустить замер климата",
                              callback_data="admin_broadcast_climate_start")],
        [InlineKeyboardButton("📈 Статистика опросов", callback_data="admin_stats")],
        [InlineKeyboardButton("💬 Обратная связь по боту", callback_data="submit_bot_feedback")]
    ])


def get_back_to_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data=settings.CALLBACK_ADMIN_BACK)]])


def get_manager_menu_keyboard(pending_feedback_count: int = 0) -> InlineKeyboardMarkup:
    feedback_button_text = "📝 Оставить ОС по кандидатам"
    if pending_feedback_count > 0:
        feedback_button_text += f" ({pending_feedback_count} ❗️)"

    buttons = [
        [InlineKeyboardButton(feedback_button_text, callback_data="manager_feedback")],
        [InlineKeyboardButton("💬 Обратная связь по боту", callback_data="submit_bot_feedback")]
    ]
    return InlineKeyboardMarkup(buttons)


def get_pending_feedback_keyboard(pending_tasks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons = []
    for task in pending_tasks:
        buttons.append([InlineKeyboardButton(f"👤 {task['name']}", callback_data=f"fb_{task['id']}")])
    buttons.append([InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def get_shift_date_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    today = get_now().date()
    for i in range(6):
        target_date = today + timedelta(days=i)
        day_name = target_date.strftime("%a").capitalize()
        date_str = target_date.strftime("%d %b")
        text = f"{day_name}, {date_str}"
        callback_data = f"shift_date_{target_date.isoformat()}"
        buttons.append(InlineKeyboardButton(text, callback_data=callback_data))

    keyboard_layout = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard_layout.append([
        InlineKeyboardButton("Другая дата (ввести вручную)", callback_data="shift_date_other")
    ])

    return InlineKeyboardMarkup(keyboard_layout)