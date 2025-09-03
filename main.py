import asyncio
import logging
import os
import time
import psutil
from datetime import timedelta

from telegram import Update
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
from aiohttp import web

from models import AdminState, MainMenuState, FeedbackState, ManagerFeedbackState
from core import settings, database, g_sheets, monitoring
from core.logging_config import setup_logging
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
from handlers.admin import (
    list_managers, add_manager_start, remove_manager_start,
    broadcast_climate_start, admin_panel_start, add_restaurant_chosen,
    add_id_received, handle_broadcast_confirmation,
    show_stats, admin_list_pending_candidates, remove_manager_selected,
    handle_admin_delete_candidate, handle_admin_delete_confirmation,
    manage_employees_start, toggle_employee_status_handler, manage_managers_start,
    show_employees_paginated, handle_candidate_action_menu
)
from handlers.feedback import (
    candidate_feedback_conversation_handler,
    onboarding_followup_conversation_handler,
)
from handlers.main_menu import start, handle_manager_feedback_button, handle_feedback_candidate_selection, \
    receive_and_forward_feedback, start_feedback
from handlers.manager_feedback_flow import (
    start_manager_feedback_flow,
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

background_tasks = set()
stop_event = asyncio.Event()


async def cleanup_bot_data(context: ContextTypes.DEFAULT_TYPE):
    """Periodically cleans up old data from bot_data."""
    now = time.time()
    bot_data = context.bot_data
    cleanup_count = 0
    # Cleanup candidate_check_info (older than 30 days)
    if 'candidate_check_info' in bot_data:
        thirty_days_ago = now - timedelta(days=30).total_seconds()
        keys_to_delete = [
            cid for cid, data in bot_data['candidate_check_info'].items()
            if data.get('timestamp', 0) < thirty_days_ago
        ]
        for key in keys_to_delete:
            del bot_data['candidate_check_info'][key]
            cleanup_count += 1

    if cleanup_count > 0:
        logger.info(f"Bot data cleanup: Removed {cleanup_count} old entries.")


async def post_init(application: Application):
    global background_tasks, stop_event
    application.bot_data.setdefault("last_telegram_update_ts", time.time())
    await database.init_db()
    if not all([settings.TOKEN, settings.GOOGLE_CREDENTIALS_JSON, settings.SPREADSHEET_ID, settings.ADMIN_IDS]):
        logger.critical("CRITICAL ERROR: One or more required environment variables are missing or invalid.")
    agc_manager = await g_sheets.init_google_sheets_client()
    if not agc_manager:
        logger.warning("Google Sheets client failed to initialize. Recording to sheets is disabled.")
    loop = asyncio.get_running_loop()
    if agc_manager:
        writer_task = loop.create_task(
            g_sheets.batch_writer_task(application, stop_event, agc_manager, application.bot_data)
        )
        background_tasks.add(writer_task)
        writer_task.add_done_callback(background_tasks.discard)
    heartbeat_task = loop.create_task(
        monitoring.heartbeat_task(application, stop_event, application.bot_data)
    )
    background_tasks.add(heartbeat_task)
    heartbeat_task.add_done_callback(background_tasks.discard)
    http_app = web.Application()
    http_app.router.add_get("/ping", monitoring.handle_http_ping)
    http_server_task = loop.create_task(
        monitoring.start_http_server(http_app, stop_event)
    )
    background_tasks.add(http_server_task)
    http_server_task.add_done_callback(background_tasks.discard)

    # Schedule the periodic cleanup task
    if application.job_queue:
        application.job_queue.run_repeating(
            cleanup_bot_data,
            interval=timedelta(hours=24),
            first=timedelta(seconds=10),
            name="cleanup_bot_data"
        )
        logger.info("Scheduled periodic bot_data cleanup.")

    logger.info(f"Bot post-initialization complete. {len(background_tasks)} background tasks started.")


async def on_shutdown(application: Application):
    global background_tasks, stop_event
    logger.info("--- Initiating graceful shutdown sequence ---")
    if not stop_event.is_set():
        stop_event.set()
    if background_tasks:
        logger.info(f"Cancelling {len(background_tasks)} background tasks...")
        for task in list(background_tasks):
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*background_tasks, return_exceptions=True)
            logger.info("All background tasks cancelled.")
        except asyncio.CancelledError:
            logger.info("Gather was cancelled, this is expected.")
        background_tasks.clear()
    if os.path.exists(settings.PID_FILE):
        try:
            os.remove(settings.PID_FILE)
            logger.info(f"PID file {settings.PID_FILE} removed.")
        except OSError:
            pass
    logger.info("--- Bot shutdown complete ---")


async def reason_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["feedback_reason"] = update.message.text
    await send_or_edit_message(update, context, "Спасибо, твоя обратная связь сохранена! 🙏")
    await process_manager_feedback(context, update.effective_user)
    return ConversationHandler.END


def main() -> None:
    logger.info("--- Bot Starting Up ---")

    if os.path.exists(settings.PID_FILE):
        try:
            with open(settings.PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                logger.warning(
                    f"Stale PID file found for a running process {old_pid}. Monitor should handle it. Continuing...")
            else:
                logger.warning(f"Stale PID file found for a dead process. Removing {settings.PID_FILE}.")
                os.remove(settings.PID_FILE)
        except (ValueError, OSError) as e:
            logger.error(f"Error handling stale PID file: {e}. Removing it.")
            try:
                os.remove(settings.PID_FILE)
            except OSError:
                pass

    try:
        pid = os.getpid()
        with open(settings.PID_FILE, "w") as f:
            f.write(str(pid))
        logger.info(f"Bot process started with PID: {pid}.")
    except Exception as e:
        logger.critical(f"Could not write PID file '{settings.PID_FILE}': {e}.")
        return

    persistence = PicklePersistence(filepath=settings.PERSISTENCE_FILE)
    application = (
        Application.builder()
        .token(settings.TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(on_shutdown)
        .read_timeout(30).write_timeout(30).connect_timeout(30)
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

    application.add_handler(candidate_feedback_conversation_handler)
    application.add_handler(onboarding_followup_conversation_handler)
    application.add_handler(manager_registration_handler)
    application.add_handler(climate_survey_conversation_handler)
    application.add_handler(exit_interview_conversation_handler)

    admin_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", admin_panel_start, filters=filters.User(user_id=settings.ADMIN_IDS)),
            CallbackQueryHandler(admin_panel_start, pattern=f"^{settings.CALLBACK_ADMIN_BACK}$")
        ],
        states={
            AdminState.MENU: [
                CallbackQueryHandler(manage_managers_start, pattern="admin_manage_managers"),
                CallbackQueryHandler(manage_employees_start, pattern="admin_manage_employees"),
                CallbackQueryHandler(admin_list_pending_candidates, pattern="admin_pending_candidates"),
                CallbackQueryHandler(list_managers, pattern="admin_list_managers"),
                CallbackQueryHandler(broadcast_climate_start, pattern="admin_broadcast_climate_start"),
                CallbackQueryHandler(show_stats, pattern="admin_stats"),
            ],
            AdminState.MANAGE_MANAGERS: [
                CallbackQueryHandler(add_manager_start, pattern="admin_add_manager_start"),
                CallbackQueryHandler(remove_manager_start, pattern="admin_remove_manager_start"),
                CallbackQueryHandler(list_managers, pattern="admin_list_managers"),
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
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK),
            ],
            AdminState.AWAIT_ADD_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND | filters.FORWARDED, add_id_received),
                CallbackQueryHandler(add_manager_start, pattern="admin_add_manager_start"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK)
            ],
            AdminState.AWAIT_REMOVAL_ID: [
                CallbackQueryHandler(remove_manager_selected, pattern="^admin_remove_mgr_"),
                CallbackQueryHandler(manage_managers_start, pattern="admin_manage_managers"),
                CallbackQueryHandler(admin_panel_start, pattern=settings.CALLBACK_ADMIN_BACK),
            ],
            AdminState.BROADCAST_CONFIRM: [
                CallbackQueryHandler(handle_broadcast_confirmation,
                                     pattern=f"^(admin_broadcast_confirm|admin_broadcast_cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", admin_panel_start, filters=filters.User(user_id=settings.ADMIN_IDS))
        ],
        persistent=True,
        name="admin_conv",
        per_message=False,
    )

    main_conversation_handler = ConversationHandler(
        entry_points=[
            recruitment_conversation_handler,
            onboarding_conversation_handler,
            CommandHandler("start", start, filters=~filters.User(user_id=settings.ADMIN_IDS)),
            CommandHandler("feedback", start_feedback),
            CallbackQueryHandler(start, pattern=f"^{settings.CALLBACK_GO_TO_MAIN_MENU}$"),
        ],
        states={
            MainMenuState.MAIN: [
                CallbackQueryHandler(handle_manager_feedback_button, pattern="^manager_feedback$"),
                CallbackQueryHandler(start, pattern="^main_menu$"),
            ],
            MainMenuState.AWAITING_FEEDBACK_CHOICE: [
                CallbackQueryHandler(handle_candidate_action_menu, pattern="^cand_act_"),
                CallbackQueryHandler(handle_feedback_candidate_selection, pattern="^fb_"),
                CallbackQueryHandler(handle_admin_delete_candidate, pattern="^cand_del_"),
                CallbackQueryHandler(admin_list_pending_candidates, pattern="admin_pending_candidates"),
                CallbackQueryHandler(start, pattern="^main_menu$"),
                CallbackQueryHandler(admin_panel_start, pattern="^admin_back_to_menu$"),
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
        per_message=False,
    )

    application.add_handler(admin_conversation_handler)
    application.add_handler(feedback_submission_handler)
    application.add_handler(main_conversation_handler)
    application.add_error_handler(error_handler)
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()