import html
import logging
import asyncio

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ParseMode

from models import OnboardingState
from core import settings, database, stickers
from utils.helpers import (
    get_user_data_from_update,
    safe_answer_callback_query,
    send_or_edit_message,
    add_to_sheets_queue,
    add_user_to_interacted,
    get_now,
    format_user_for_sheets,
    cleanup_chat
)
from utils.keyboards import (
    RESTAURANT_OPTIONS,
    ONBOARDING_POSITION_OPTIONS,
    POSITION_LINKS,
    INTEREST_RATING_OPTIONS,
    build_inline_keyboard
)
from handlers.common import cancel, prompt_to_use_button

logger = logging.getLogger(__name__)


async def start_onboarding_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> OnboardingState:
    user_id, user_name, _ = get_user_data_from_update(update)
    await add_user_to_interacted(user_id, context)
    context.user_data.clear()
    context.user_data['conversations'] = {'onboarding_conv': True}

    logger.info(f"User {user_name} ({user_id}) started onboarding flow.")

    restaurant_code = ""
    restaurant_name = "Не указан"

    if update.callback_query:
        await update.callback_query.message.delete()

    if update.callback_query:
        restaurant_code = await database.get_candidate_restaurant(user_id) or ""
        restaurant_name = next((name for name, code in RESTAURANT_OPTIONS if code.endswith(restaurant_code)),
                               "Не указан")
    elif context.args:
        param = context.args[0]
        if not param or not param.startswith("onboard_"):
            if update.message:
                await update.message.reply_text("Ошибка в ссылке. Пожалуйста, обратитесь к менеджеру.")
            return ConversationHandler.END
        restaurant_code = param.replace("onboard_", "")
        restaurant_name = next((name for name, r_code in RESTAURANT_OPTIONS if r_code.endswith(restaurant_code)),
                               "Не указан")

    if restaurant_name == "Не указан":
        if update.effective_message:
            await update.effective_message.reply_text(
                "Не удалось определить ресторан. Пожалуйста, обратитесь к менеджеру.")
        return ConversationHandler.END

    context.user_data['onboarding_restaurant'] = restaurant_name
    context.user_data['onboarding_restaurant_code'] = restaurant_code
    context.user_data['chat_id'] = update.effective_chat.id

    await context.bot.send_sticker(chat_id=user_id, sticker=stickers.GREETING_TEAM)
    await asyncio.sleep(0.5)

    keyboard = build_inline_keyboard(ONBOARDING_POSITION_OPTIONS, columns=2)
    message_text = (
        f"Поздравляем с первой сменой в «Марчеллис»! 🎉\n\n"
        f"Ты начинаешь свой путь в ресторане: <b>{html.escape(restaurant_name)}</b>.\n\n"
        "Первое впечатление — самое важное, и мы очень хотим его узнать!\n\n"
        "<b>Шаг 1/4:</b> Напомни, пожалуйста, на какую должность ты к нам присоединился(ась)?"
    )

    await send_or_edit_message(update, context, message_text, keyboard)

    return OnboardingState.POSITION


async def position_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> OnboardingState:
    query = update.callback_query
    await safe_answer_callback_query(query)

    if query.data == "onboard_pos_Other":
        await send_or_edit_message(update, context, "Пожалуйста, напиши свою должность:")
        return OnboardingState.AWAIT_OTHER_POSITION

    chosen_button = next((b for b in ONBOARDING_POSITION_OPTIONS if b[1] == query.data), None)
    position = chosen_button[0] if chosen_button else "Не указана"
    context.user_data["onboarding_position"] = position

    message_text = (
        f"Позиция: <b>{html.escape(position)}</b>. ✨\n\n"
        "<b>Шаг 2/4:</b>\n"
        "Поделись своим <b>первым впечатлением</b> после ознакомительной смены! Что понравилось, что удивило?"
    )
    await send_or_edit_message(update, context, message_text)
    return OnboardingState.IMPRESSION


async def other_position_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> OnboardingState:
    position = update.message.text.strip()
    context.user_data["onboarding_position"] = position
    context.user_data["onboarding_position_is_other"] = True

    message_text = (
        f"Позиция: <b>{html.escape(position)}</b>. ✨\n\n"
        "<b>Шаг 2/4:</b>\n"
        "Поделись своим <b>первым впечатлением</b> после ознакомительной смены! Что понравилось, что удивило?"
    )
    await send_or_edit_message(update, context, message_text)
    return OnboardingState.IMPRESSION


async def impression_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> OnboardingState:
    context.user_data["onboarding_impression"] = update.message.text.strip()
    keyboard = build_inline_keyboard(INTEREST_RATING_OPTIONS, columns=5)
    text = (
        "Спасибо за отзыв!\n\n"
        "<b>Шаг 3/4:</b>\n"
        "А теперь оцени свою <b>заинтересованность</b> в продолжении стажировки у нас по шкале от 1 (совсем не интересно) до 10 (очень хочу продолжить)."
    )
    await send_or_edit_message(update, context, text, keyboard)
    return OnboardingState.INTEREST_RATING


async def interest_rating_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> OnboardingState:
    query = update.callback_query
    await safe_answer_callback_query(query)
    rating = query.data.replace("onboard_rate_", "")
    context.user_data["onboarding_interest_level"] = rating
    text = (
        f"Твоя оценка: <b>{rating}</b>. Принято!\n\n"
        "<b>Шаг 4/4:</b>\n"
        "Что больше всего <b>повлияло на твою оценку</b>?"
    )
    await send_or_edit_message(update, context, text, None)
    return OnboardingState.INTEREST_REASON


async def interest_reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["onboarding_interest_reason"] = update.message.text.strip()
    await send_or_edit_message(update, context, "Обрабатываем твой ответ...")

    user_id, user_name, _ = get_user_data_from_update(update)
    user = update.effective_user
    data = context.user_data
    restaurant_code = data.get("onboarding_restaurant_code")
    is_other_position = data.get("onboarding_position_is_other", False)
    user_link = format_user_for_sheets(user_id, user_name, user.username)

    row_data = [
        get_now().strftime("%Y-%m-%d %H:%M:%S"),
        user_link,
        data.get("onboarding_restaurant"),
        data.get("onboarding_position"),
        data.get("onboarding_impression"),
        data.get("onboarding_interest_level"),
        data.get("onboarding_interest_reason")
    ]
    await add_to_sheets_queue(settings.ONBOARDING_SHEET_NAME, row_data)
    await database.log_survey_completion('onboarding', user_id, restaurant_code)

    interest_level_str = data.get("onboarding_interest_level", "0")
    message_title = "📝 <b>Новый отзыв после ознакомительной смены</b>"
    try:
        if int(interest_level_str) < 6:
            message_title = f"❗️🚩 <b>Низкая заинтересованность после смены! ({interest_level_str}/10)</b>"
    except (ValueError, TypeError):
        pass

    admin_message = (
        f"{message_title}\n\n"
        f"<b>Кандидат:</b> {html.escape(user_name)}\n"
        f"<b>Ресторан:</b> {html.escape(data.get('onboarding_restaurant', 'N/A'))}\n"
        f"<b>Должность:</b> {html.escape(data.get('onboarding_position', 'N/A'))}\n\n"
        f"<b>⭐ Уровень интереса:</b> {interest_level_str}/10\n"
        f"<b>💬 Причина оценки:</b>\n<pre>{html.escape(data.get('onboarding_interest_reason', 'N/A'))}</pre>\n\n"
        f"<b>📝 Впечатление от смены:</b>\n<pre>{html.escape(data.get('onboarding_impression', 'N/A'))}</pre>"
    )

    if settings.ADMIN_IDS:
        for admin_id in settings.ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=admin_message, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Failed to send onboarding feedback summary to admin {admin_id}: {e}")

    await cleanup_chat(context, user_id)

    position = "Другое" if is_other_position else data.get("onboarding_position", "сотрудника")
    links_data = POSITION_LINKS.get(position, [])
    critical_links = [item for item in links_data if item.get("is_critical")]
    other_links = [item for item in links_data if not item.get("is_critical") and "url" in item]
    additional_message = next((item.get("additional_message") for item in links_data if "additional_message" in item),
                              "Желаем тебе успешного старта и увлекательной адаптации!")
    final_position_text = data.get("onboarding_position")

    final_message_parts = [
        f"🎉 <b>Спасибо за обратную связь! Добро пожаловать в команду!</b>\n\n"
        f"Вот несколько полезных ссылок для позиции '<b>{html.escape(final_position_text)}</b>':\n"
    ]

    if critical_links:
        final_message_parts.append("\n<b>❗️ Обязательно к изучению:</b>")
        for link in critical_links:
            final_message_parts.append(f"🔗 <a href='{link['url']}'>{html.escape(link['name'])}</a>")

    if other_links:
        final_message_parts.append("\n<b>Полезные материалы:</b>")
        for link in other_links:
            final_message_parts.append(f"🔗 <a href='{link['url']}'>{html.escape(link['name'])}</a>")

    final_message_parts.append(f"\n\n🔔 <b>Важный момент:</b> {html.escape(additional_message)}\n\n")

    final_message = "\n".join(final_message_parts)

    await context.bot.send_sticker(chat_id=user_id, sticker=stickers.ONBOARDING_INFO_DOG)
    await asyncio.sleep(0.5)
    await context.bot.send_message(
        user_id,
        final_message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    context.user_data.clear()
    return ConversationHandler.END


onboarding_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_onboarding_flow, filters=filters.Regex(r'onboard_'))],
    states={
        OnboardingState.POSITION: [
            CallbackQueryHandler(position_selected, pattern="^onboard_pos_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)
        ],
        OnboardingState.AWAIT_OTHER_POSITION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, other_position_received)],
        OnboardingState.IMPRESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, impression_received)],
        OnboardingState.INTEREST_RATING: [
            CallbackQueryHandler(interest_rating_received, pattern="^onboard_rate_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_to_use_button)
        ],
        OnboardingState.INTEREST_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, interest_reason_received)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="onboarding_conv",
    persistent=True,
    per_message=False,
    conversation_timeout=settings.CONVERSATION_TIMEOUT_SECONDS
)