import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path
import nltk

NLTK_DATA_PATH = Path(__file__).parent / "nltk_data"
if NLTK_DATA_PATH.exists():
    nltk.data.path.append(str(NLTK_DATA_PATH))

from telegram import Update, BotCommand, BotCommandScopeChat
from telegram.ext import (
    Application,
    PicklePersistence,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    TypeHandler,
    ConversationHandler,
    JobQueue,
    MessageHandler,
    filters,
    ContextTypes,
)

from models import AdminState, MainMenuState, FeedbackState, ManagerFeedbackState
from core import settings, database, g_sheets
from core.logging_config import setup_logging
from core.monitoring import heartbeat_task
from handlers.common import error_handler, update_timestamp_handler, cancel
from handlers.recruitment import recruitment_conversation_handler, show_full_recruitment_report, \
    send_candidate_check_info
from handlers.onboarding import onboarding_conversation_handler
from handlers.exit_interview import (
    exit_interview_conversation_handler,
    chat_member_handler,
    quit_clarification_handler
)
from handlers.climate_survey import climate_survey_conversation_handler
from handlers.bot_feedback import feedback_submission_handler
from handlers.manager import manager_registration_handler, handle_manager_approval
from handlers.director import director_conversation_handler
from handlers.employee_registration import employee_registration_handler
from handlers.admin import (
    add_manager_start, remove_manager_start,
    broadcast_climate_start, admin_panel_start, add_restaurant_chosen,
    add_id_received, handle_broadcast_confirmation,
    show_stats, admin_list_pending_candidates, remove_manager_selected,
    handle_admin_delete_candidate, handle_admin_delete_confirmation,
    manage_employees_start, toggle_employee_status_handler, manage_managers_start,
    show_employees_paginated, handle_candidate_action_menu, manage_directors_start, add_director_start,
    remove_director_start, add_director_restaurant_chosen, add_director_id_received, remove_director_selected, get_invite_links
)
from handlers.admin_analytics import (
    show_analytics_menu, show_funnel_analytics, show_salary_analytics,
    show_exit_analytics, show_sources_analytics, show_experience_analytics,
    show_climate_analytics, show_enps_by_position_analytics
)
from handlers.feedback import (
    candidate_feedback_conversation_handler,
    onboarding_followup_conversation_handler,
)
from handlers.main_menu import start, handle_manager_feedback_button, handle_feedback_candidate_selection, \
    receive_and_forward_feedback, start_feedback, show_surveys
from handlers.manager_feedback_flow import (
    decision_received,
    shift_date_received,
    manual_shift_date_received,
    shift_time_received,
    comment_received,
    skip_comment,
    process_manager_feedback
)
from utils.helpers import send_or_edit_message

logger = setup_logging(__name__)


async def post_init(application: Application):
    logger.info("Running post-initialization tasks...")

    if not all([settings.TOKEN, settings.SPREADSHEET_ID, settings.ADMIN_IDS]):
        logger.critical("CRITICAL ERROR: TOKEN, SPREADSHEET_ID, or ADMIN_IDS are missing.")
        return

    agc_manager = await g_sheets.init_google_sheets_client()
    if not agc_manager:
        logger.warning("Google Sheets client failed to initialize. Recording to sheets is disabled.")
    else:
        logger.info("Google Sheets client initialized successfully.")
        application.bot_data['agc_manager'] = agc_manager

    if application.job_queue:
        if agc_manager:
            application.job_queue.run_repeating(
                g_sheets.sheets_writer_job,
                interval=timedelta(seconds=settings.BATCH_INTERVAL),
                first=10.0,
                name="gspread_writer_job"
            )
            logger.info("Google Sheets writer job scheduled via JobQueue.")
        else:
            logger.warning("Google Sheets writer job NOT scheduled because client failed to initialize.")

        application.job_queue.run_repeating(
            heartbeat_task,
            interval=timedelta(seconds=settings.HEARTBEAT_INTERVAL_SECONDS),
            first=5.0,
            name="heartbeat_job"
        )
        logger.info("Heartbeat job scheduled via JobQueue.")
    else:
        logger.error("JobQueue is not available. Background tasks will not run.")

    logger.info("Bot post-initialization complete.")


async def on_shutdown(application: Application):
    logger.info("--- Bot shutdown sequence initiated ---")
    if os.path.exists(settings.HEARTBEAT_FILE):
        try:
            os.remove(settings.HEARTBEAT_FILE)
            logger.info("Heartbeat file removed.")
        except OSError as e:
            logger.error(f"Error removing heartbeat file on shutdown: {e}")
    logger.info("--- Bot shutdown complete ---")


async def reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["feedback_reason"] = update.message.text
    await send_or_edit_message(update, context, "Спасибо, твоя обратная связь сохранена! 🙏")
    await process_manager_feedback(context, update.effective_user)
    return ConversationHandler.END


async def test_sheets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in settings.ADMIN_IDS:
        return
    await g_sheets.test_google_sheets_connection(context)
    await update.message.reply_text("Запустил тестовую запись в Google Sheets. Проверь логи и таблицу 'Тест'")


async def main() -> None:
    logger.info("--- Bot Starting Up ---")

    await database.init_db()
    logger.info("Database initialization complete.")
    logger.info(f"Bot process started with PID: {os.getpid()}.")

    persistence = PicklePersistence(filepath=settings.PERSISTENCE_FILE)

    application = (
        Application.builder()
        .token(settings.TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(on_shutdown)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .job_queue(JobQueue())
        .build()
    )

    application.add_handler(TypeHandler(Update, update_timestamp_handler), group=-1)

    application.add_handler(
        CallbackQueryHandler(handle_manager_approval,
                             pattern=f"^{settings.CALLBACK_MGR_APPROVE_PREFIX}|^{settings.CALLBACK_MGR_REJECT_PREFIX}")
    )
    application.add_handler(CallbackQueryHandler(show_full_recruitment_report, pattern=f"^show_full_report_"))
    application.add_handler(CallbackQueryHandler(send_candidate_check_info, pattern=f"^check_candidate_"))
    application.add_handler(CallbackQueryHandler(handle_admin_delete_confirmation, pattern="^cand_del_confirm_"))
    application.add_handler(chat_member_handler)
    application.add_handler(quit_clarification_handler)
    application.add_handler(CommandHandler("test_sheets", test_sheets_command, filters=filters.User(user_id=settings.ADMIN_IDS)))
    application.add_handler(CommandHandler("invites", get_invite_links, filters=filters.User(user_id=settings.ADMIN_IDS)))
    application.add_handler(CommandHandler("surveys", show_surveys))

    application.add_handler(recruitment_conversation_handler)
    application.add_handler(onboarding_conversation_handler)
    application.add_handler(candidate_feedback_conversation_handler)
    application.add_handler(onboarding_followup_conversation_handler)
    application.add_handler(manager_registration_handler)
    application.add_handler(climate_survey_conversation_handler)
    application.add_handler(exit_interview_conversation_handler)
    application.add_handler(feedback_submission_handler)
    application.add_handler(director_conversation_handler)
    application.add_handler(employee_registration_handler)

    admin_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", admin_panel_start, filters=filters.User(user_id=settings.ADMIN_IDS)),
            CommandHandler("admin", admin_panel_start, filters=filters.User(user_id=settings.ADMIN_IDS)),
            CallbackQueryHandler(admin_panel_start, pattern=f"^{settings.CALLBACK_ADMIN_BACK}$"),
        ],
        states={
            AdminState.MENU: [
                CallbackQueryHandler(manage_managers_start, pattern="admin_manage_managers"),
                CallbackQueryHandler(manage_directors_start, pattern="admin_manage_directors"),
                CallbackQueryHandler(manage_employees_start, pattern="admin_manage_employees"),
                CallbackQueryHandler(admin_list_pending_candidates, pattern="admin_pending_candidates"),
                CallbackQueryHandler(broadcast_climate_start, pattern="admin_broadcast_climate_start"),
                CallbackQueryHandler(show_stats, pattern="admin_stats"),
                CallbackQueryHandler(show_analytics_menu, pattern="admin_analytics"),
            ],
            AdminState.ANALYTICS_MENU: [
                CallbackQueryHandler(show_funnel_analytics, pattern="analytics_funnel"),
                CallbackQueryHandler(show_salary_analytics, pattern="analytics_salary"),
                CallbackQueryHandler(show_exit_analytics, pattern="analytics_exit"),
                CallbackQueryHandler(show_sources_analytics, pattern="analytics_sources"),
                CallbackQueryHandler(show_experience_analytics, pattern="analytics_experience"),
                CallbackQueryHandler(show_climate_analytics, pattern="analytics_climate"),
                CallbackQueryHandler(show_enps_by_position_analytics, pattern="analytics_enps_position"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK)
            ],
            AdminState.MANAGE_MANAGERS: [
                CallbackQueryHandler(add_manager_start, pattern="admin_add_manager_start"),
                CallbackQueryHandler(remove_manager_start, pattern="admin_remove_manager_start"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK),
            ],
            AdminState.MANAGE_DIRECTORS: [
                CallbackQueryHandler(add_director_start, pattern="admin_add_director_start"),
                CallbackQueryHandler(remove_director_start, pattern="admin_remove_director_start"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK),
            ],
            AdminState.CHOOSE_EMPLOYEE_RESTAURANT: [
                CallbackQueryHandler(show_employees_paginated, pattern="^list_emp_res_"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK),
            ],
            AdminState.LIST_EMPLOYEES_PAGINATED: [
                CallbackQueryHandler(toggle_employee_status_handler, pattern="^adm_tgl_emp_"),
                CallbackQueryHandler(show_employees_paginated, pattern="^list_emp_res_"),
                CallbackQueryHandler(manage_employees_start, pattern="admin_manage_employees"),
            ],
            AdminState.CHOOSE_ADD_RESTAURANT: [
                CallbackQueryHandler(add_restaurant_chosen, pattern="^res_"),
                CallbackQueryHandler(manage_managers_start, pattern="admin_manage_managers"),
            ],
             AdminState.CHOOSE_ADD_DIRECTOR_RESTAURANT: [
                CallbackQueryHandler(add_director_restaurant_chosen, pattern="^res_"),
                CallbackQueryHandler(manage_directors_start, pattern="admin_manage_directors"),
            ],
            AdminState.AWAIT_ADD_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND | filters.FORWARDED, add_id_received),
                CallbackQueryHandler(add_manager_start, pattern="admin_add_manager_start"),
            ],
            AdminState.AWAIT_ADD_DIRECTOR_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND | filters.FORWARDED, add_director_id_received),
                CallbackQueryHandler(add_director_start, pattern="admin_add_director_start"),
            ],
            AdminState.AWAIT_REMOVAL_ID: [
                CallbackQueryHandler(remove_manager_selected, pattern="^admin_remove_mgr_"),
                CallbackQueryHandler(manage_managers_start, pattern="admin_manage_managers"),
            ],
            AdminState.AWAIT_DIRECTOR_REMOVAL_ID: [
                CallbackQueryHandler(remove_director_selected, pattern="^admin_remove_dir_"),
                CallbackQueryHandler(manage_directors_start, pattern="admin_manage_directors"),
            ],
            AdminState.BROADCAST_CONFIRM: [
                CallbackQueryHandler(handle_broadcast_confirmation,
                                     pattern=f"^(admin_broadcast_confirm|admin_broadcast_cancel)$"),
            ],
            AdminState.AWAIT_CANDIDATE_ACTION: [
                CallbackQueryHandler(handle_candidate_action_menu, pattern="^cand_act_"),
                CallbackQueryHandler(handle_admin_delete_candidate, pattern="^cand_del_"),
                CallbackQueryHandler(admin_list_pending_candidates, pattern="admin_pending_candidates")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", admin_panel_start, filters=filters.User(user_id=settings.ADMIN_IDS))
        ],
        persistent=True,
        name="admin_conv",
        per_user=True,
        per_chat=True,
        per_message=False,
    )

    main_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start, filters=~filters.User(user_id=settings.ADMIN_IDS)),
            CommandHandler("feedback", start_feedback),
            CallbackQueryHandler(start, pattern=f"^{settings.CALLBACK_GO_TO_MAIN_MENU}$"),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & ~filters.User(user_id=settings.ADMIN_IDS),
                start_feedback),
        ],
        states={
            MainMenuState.MAIN: [
                CallbackQueryHandler(handle_manager_feedback_button, pattern="^manager_feedback$"),
                CallbackQueryHandler(start, pattern="^main_menu$"),
            ],
            MainMenuState.AWAITING_FEEDBACK_CHOICE: [
                CallbackQueryHandler(handle_feedback_candidate_selection, pattern="^fb_"),
                CallbackQueryHandler(start, pattern="^main_menu$"),
            ],
            FeedbackState.AWAITING_FEEDBACK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_and_forward_feedback)
            ],
            ManagerFeedbackState.AWAITING_DECISION: [
                CallbackQueryHandler(decision_received, pattern=f"^{settings.CALLBACK_MGR_FEEDBACK_PREFIX}")
            ],
            ManagerFeedbackState.AWAITING_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reason_received)],
            ManagerFeedbackState.AWAITING_SHIFT_DATE: [
                CallbackQueryHandler(shift_date_received, pattern="^shift_date_")
            ],
            ManagerFeedbackState.AWAITING_MANUAL_SHIFT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_shift_date_received)
            ],
            ManagerFeedbackState.AWAITING_SHIFT_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, shift_time_received)
            ],
            ManagerFeedbackState.AWAITING_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, comment_received),
                CallbackQueryHandler(skip_comment, pattern="^skip_comment$"),
                CommandHandler("skip", skip_comment),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start, filters=~filters.User(user_id=settings.ADMIN_IDS))
        ],
        persistent=True,
        name="main_conversation_handler",
        per_user=True,
        per_chat=True,
        per_message=False,
    )

    application.add_handler(admin_conversation_handler)
    application.add_handler(main_conversation_handler)
    application.add_error_handler(error_handler)

    logger.info("Starting bot...")

    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user or system.")
    except Exception as e:
        logger.critical(f"Bot failed to run due to an unhandled exception: {e}", exc_info=True)