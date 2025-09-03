import html
import logging
from enum import Enum

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

from models import ClimateState
from core import settings, database
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_or_edit_message,
    add_to_sheets_queue,
    get_now
)
from utils.keyboards import (
    RESTAURANT_OPTIONS,
    GENDER_OPTIONS,
    EXIT_POSITION_OPTIONS,
    YES_NO_OPTIONS_CLIMATE,
    YES_NO_MAYBE_OPTIONS,
    build_inline_keyboard
)
from handlers.common import cancel, prompt_to_use_button

logger = logging.getLogger(__name__)


async def start_climate_survey_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    context.user_data.clear()
    context.user_data['_in_climate_survey'] = True
    context.user_data['chat_id'] = query.message.chat_id

    greeting_text = (
        "Привет! Мы стремимся сделать «Марчеллис» лучшим местом для работы и профессионального роста. 🚀\n\n"
        "Успех нашей компании — это заслуга всей команды, и твое мнение играет в этом ключевую роль.\n\n"
        "Помоги нам стать ещё лучше! Пройди, пожалуйста, этот небольшой <b>анонимный опрос</b>. "
        "Твои честные ответы — это ценный вклад в наше общее развитие.")
    await send_or_edit_message(update, context, greeting_text, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Да", callback_data="climate_employed_yes")],
        [InlineKeyboardButton("Нет", callback_data="climate_employed_no")],
    ])
    message_text = "Для начала, подскажи, пожалуйста, <b>работаешь ли ты сейчас в компании?</b>"
    await send_or_edit_message(update, context, message_text, keyboard)

    return ClimateState.AWAIT_EMPLOYMENT_STATUS


async def climate_employment_status_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState | int:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if query.data == "climate_employed_no":
        await send_or_edit_message(update, context, "Спасибо за честность. Этот опрос предназначен для действующих сотрудников. Хорошего дня!")
        context.user_data.clear()
        return ConversationHandler.END

    keyboard = build_inline_keyboard(RESTAURANT_OPTIONS, columns=2)
    message_text = "Отлично! Тогда начнем.\n\n<b>Вопрос 1/17</b>\nВ каком <b>ресторане</b> ты работаешь?"
    await send_or_edit_message(update, context, message_text, keyboard)
    return ClimateState.RESTAURANT


async def climate_restaurant_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    restaurant = next((b[0] for b in RESTAURANT_OPTIONS if b[1] == query.data), "N/A")
    restaurant_code = query.data.replace("res_", "")
    context.user_data["climate_restaurant"] = restaurant
    context.user_data["climate_restaurant_code"] = restaurant_code

    keyboard = build_inline_keyboard(GENDER_OPTIONS, columns=2)
    text = f"Ресторан: <b>{html.escape(restaurant)}</b>.\n\n<b>Вопрос 2/17</b>\nУкажи, пожалуйста, свой <b>пол</b>:"
    await send_or_edit_message(update, context, text, keyboard)
    return ClimateState.GENDER


async def climate_gender_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    gender = next((b[0] for b in GENDER_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["climate_gender"] = gender.replace('👨', '').replace('👩', '').strip()

    keyboard = build_inline_keyboard(EXIT_POSITION_OPTIONS, columns=2)
    text = "<b>Вопрос 3/17</b>\nУкажи, пожалуйста, свою <b>должность</b>:"
    await send_or_edit_message(update, context, text, keyboard)
    return ClimateState.POSITION


async def climate_position_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    position = next((b[0] for b in EXIT_POSITION_OPTIONS if b[1] == query.data), "N/A")
    context.user_data["climate_position"] = position

    keyboard = build_inline_keyboard(YES_NO_OPTIONS_CLIMATE, columns=2)
    text = f"Должность: <b>{html.escape(position)}</b>.\n\n<b>Вопрос 4/17</b>\nГотов(а) ли ты <b>порекомендовать</b> нашу компанию как работодателя друзьям или близким?"
    await send_or_edit_message(update, context, text, keyboard)
    return ClimateState.RECOMMEND


async def climate_recommend_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    recommend = "Да" if query.data == "climate_yes" else "Нет"
    context.user_data["climate_recommend"] = recommend

    text = f"Твой ответ: <b>{html.escape(recommend)}</b>.\n\n<b>Вопрос 5/17</b>\n<b>Почему</b> ты так считаешь? (кратко)"
    await send_or_edit_message(update, context, text)
    return ClimateState.RECOMMEND_REASON


async def climate_recommend_reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> ClimateState:
    context.user_data["climate_recommend_reason"] = update.message.text.strip()
    keyboard = build_inline_keyboard(YES_NO_MAYBE_OPTIONS, columns=2)
    text = "Спасибо! Теперь серия коротких вопросов.\n\n" \
           "<b>Вопрос 6/17</b>\nЯ <b>знаю</b>, что от меня ожидается на работе."
    await send_or_edit_message(update, context, text, keyboard)
    return ClimateState.EXPECTATIONS


async def climate_generic_yes_no_maybe_handler(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        current_data_key: str,
        next_state: Enum | None,
        next_question_number: int,
        next_question_text: str,
) -> Enum | int:
    query = update.callback_query
    await safe_answer_callback_query(query)

    answer_text = next((b[0] for b in YES_NO_MAYBE_OPTIONS if b[1] == query.data), "N/A")
    context.user_data[current_data_key] = answer_text.replace('✅ ', '').replace('☑️ ', '').replace('❌ ', '').replace(
        '🚫 ', '').strip()

    if next_state:
        keyboard = build_inline_keyboard(YES_NO_MAYBE_OPTIONS, columns=2)
        text = f"<b>Вопрос {next_question_number}/17</b>\n\n{next_question_text}"
        await send_or_edit_message(update, context, text, keyboard)
        return next_state
    else:
        await climate_final_question_answered(update, context)
        return ConversationHandler.END


async def climate_expectations_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_expectations", ClimateState.BEST_ABILITY, 7,
        "У меня есть возможность делать то, что я делаю <b>лучше всего</b> 💪",
    )


async def climate_best_ability_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_best_ability", ClimateState.PRAISE, 8,
        "В последние семь дней кто-то на работе <b>похвалил</b> меня или оценил мою работу 🎉",
    )


async def climate_praise_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_praise", ClimateState.DEVELOPMENT_CARE, 9,
        "Мой руководитель или кто-то другой на работе <b>заботится</b> о моем <b>развитии</b> 🌱",
    )


async def climate_development_care_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_development_care", ClimateState.OPINION, 10,
        "На работе у меня есть возможность <b>высказать свое мнение</b> 🗣️",
    )


async def climate_opinion_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_opinion", ClimateState.COLLEAGUE_SUCCESS, 11,
        "Мои <b>коллеги</b> настроены на <b>успех</b> 🤝",
    )


async def climate_colleague_success_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_colleague_success", ClimateState.MISSION, 12,
        "У нас на работе существует <b>миссия или цель</b>, которая меня вдохновляет 🎯",
    )


async def climate_mission_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_mission", ClimateState.IMPORTANCE, 13,
        "Я чувствую, что моя работа <b>важна</b> ⭐",
    )


async def climate_importance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_importance", ClimateState.GROWTH_OPPORTUNITY, 14,
        "У меня есть возможность <b>развиваться и расти</b> в своей карьере 📈",
    )


async def climate_growth_opportunity_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_growth_opportunity", ClimateState.SUPPORT, 15,
        "Я получаю <b>поддержку</b> от своего руководителя в выполнении своей работы 🤗",
    )


async def climate_support_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_support", ClimateState.FRIENDS, 16,
        "На работе у меня есть <b>хорошие друзья</b> 😊",
    )


async def climate_friends_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Enum | int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_friends", ClimateState.TEAM_PART, 17,
        "В своей работе я чувствую себя <b>частью команды</b> 👥",
    )


async def climate_team_part_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await climate_generic_yes_no_maybe_handler(
        update, context, "climate_team_part", None, 0, ""
    )


async def climate_final_question_answered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, user_name, _ = get_user_data_from_update(update)
    data = context.user_data
    restaurant_code = data.get("climate_restaurant_code")
    timestamp = get_now().strftime("%Y-%m-%d %H:%M:%S")

    q_keys = [
        "restaurant", "gender", "position", "recommend", "recommend_reason", "expectations",
        "best_ability", "praise", "development_care", "opinion", "colleague_success",
        "mission", "importance", "growth_opportunity", "support", "friends", "team_part"
    ]
    q_answers = [data.get(f"climate_{key}", "N/A") for key in q_keys]
    row_data = [timestamp, user_name] + q_answers

    await add_to_sheets_queue(settings.CLIMATE_SURVEY_SHEET_NAME, row_data)
    await database.log_survey_completion('climate', user_id, restaurant_code)

    await send_or_edit_message(
        update, context,
        "✅ <b>Опрос завершен!</b>\n\nБольшое спасибо за твой вклад! 🙏 Твои ответы помогут нам сделать рабочую среду в «Марчеллис» еще лучше."
    )
    context.user_data.clear()


climate_survey_conversation_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_climate_survey_flow, pattern=f"^{settings.CALLBACK_START_CLIMATE}$")],
    states={
        ClimateState.AWAIT_EMPLOYMENT_STATUS: [
            CallbackQueryHandler(climate_employment_status_selected, pattern="^climate_employed_")],
        ClimateState.RESTAURANT: [CallbackQueryHandler(climate_restaurant_selected, pattern="^res_"),
                                  MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.GENDER: [CallbackQueryHandler(climate_gender_selected, pattern="^climate_gender_"),
                              MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.POSITION: [CallbackQueryHandler(climate_position_selected, pattern="^exit_pos_"),
                                MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.RECOMMEND: [CallbackQueryHandler(climate_recommend_selected, pattern="^climate_(yes|no)$"),
                                 MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.RECOMMEND_REASON: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, climate_recommend_reason_received)],
        ClimateState.EXPECTATIONS: [CallbackQueryHandler(climate_expectations_selected, pattern="^climate_q_"),
                                    MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.BEST_ABILITY: [CallbackQueryHandler(climate_best_ability_selected, pattern="^climate_q_"),
                                    MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.PRAISE: [CallbackQueryHandler(climate_praise_selected, pattern="^climate_q_"),
                              MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.DEVELOPMENT_CARE: [CallbackQueryHandler(climate_development_care_selected, pattern="^climate_q_"),
                                        MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.OPINION: [CallbackQueryHandler(climate_opinion_selected, pattern="^climate_q_"),
                               MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.COLLEAGUE_SUCCESS: [
            CallbackQueryHandler(climate_colleague_success_selected, pattern="^climate_q_"),
            MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.MISSION: [CallbackQueryHandler(climate_mission_selected, pattern="^climate_q_"),
                               MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.IMPORTANCE: [CallbackQueryHandler(climate_importance_selected, pattern="^climate_q_"),
                                  MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.GROWTH_OPPORTUNITY: [
            CallbackQueryHandler(climate_growth_opportunity_selected, pattern="^climate_q_"),
            MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.SUPPORT: [CallbackQueryHandler(climate_support_selected, pattern="^climate_q_"),
                               MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.FRIENDS: [CallbackQueryHandler(climate_friends_selected, pattern="^climate_q_"),
                               MessageHandler(filters.TEXT, prompt_to_use_button)],
        ClimateState.TEAM_PART: [CallbackQueryHandler(climate_team_part_selected, pattern="^climate_q_"),
                                 MessageHandler(filters.TEXT, prompt_to_use_button)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="climate_survey_conv",
    persistent=True,
    per_message=False,
    conversation_timeout=settings.CONVERSATION_TIMEOUT_SECONDS
)