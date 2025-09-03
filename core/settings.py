import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent.resolve()
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
MOSCOW_TIMEZONE = "Europe/Moscow"

raw_admin_ids = os.getenv("ADMIN_CHAT_ID", "")
try:
    ADMIN_IDS = {int(admin_id.strip()) for admin_id in raw_admin_ids.split(',') if admin_id.strip().isdigit()}
except (ValueError, TypeError):
    ADMIN_IDS = set()

EMPLOYEES_PER_PAGE = 15

ONBOARDING_SHEET_NAME = "Ознакомительная смена"
EXIT_INTERVIEW_SHEET_NAME = "exit interview"
CLIMATE_SURVEY_SHEET_NAME = "Замер климата"
INTERVIEW_SHEET_NAME = os.getenv("INTERVIEW_SHEET_NAME", "Первичный контакт")
MANAGER_FEEDBACK_SHEET_NAME = "ОС от менеджера"
CANDIDATE_FEEDBACK_SHEET_NAME = "ОС от кандидата"
LEAVING_REASON_SHEET_NAME = "Причины ухода (авто)"
BOT_FEEDBACK_SHEET_NAME = "ОС по боту"
CANDIDATE_NOSHOW_SHEET_NAME = "Кандидаты (передумали)"


BATCH_INTERVAL = 30

EXIT_INTERVIEW_COOLDOWN_SECONDS = 60 * 60 * 24 * 7
FEEDBACK_DELAY_SECONDS = 1800
ONBOARDING_FOLLOWUP_SECONDS = 60 * 60 * 24 * 7

PID_FILE = BASE_DIR / "bot.pid"
HEARTBEAT_FILE = BASE_DIR / "heartbeat.txt"
PING_FILE = BASE_DIR / "ping.txt"
PERSISTENCE_FILE = BASE_DIR / "bot_persistence.pkl"
DATABASE_FILE = BASE_DIR / "bot_database.sqlite"

HEARTBEAT_INTERVAL_SECONDS = 30
TELEGRAM_INACTIVITY_THRESHOLD_SECONDS = 60 * 10
CONVERSATION_TIMEOUT_SECONDS = 60 * 60 * 3

ACTIVE_MESSAGE_ID_KEY = "active_message_id"

CALLBACK_START_ONBOARDING = "start_onboarding"
CALLBACK_START_EXIT = "start_exit_interview"
CALLBACK_START_CLIMATE = "start_climate_survey"
CALLBACK_CONFIRM_QUIT = "confirm_quit"
CALLBACK_DECLINE_QUIT = "decline_quit"

CALLBACK_ADMIN_LIST = "admin_list_managers"
CALLBACK_ADMIN_ADD_START = "admin_add_manager_start"
CALLBACK_ADMIN_REMOVE_START = "admin_remove_manager_start"
CALLBACK_ADMIN_BACK = "admin_back_to_menu"
CALLBACK_ADMIN_EXIT = "admin_exit_panel"
CALLBACK_ADMIN_BROADCAST_CLIMATE_START = "admin_broadcast_climate_start"
CALLBACK_ADMIN_BROADCAST_CONFIRM = "admin_broadcast_confirm"
CALLBACK_ADMIN_BROADCAST_CANCEL = "admin_broadcast_cancel"
CALLBACK_ADMIN_STATS = "admin_stats"

CALLBACK_MGR_APPROVE_PREFIX = "mgr_approve_"
CALLBACK_MGR_REJECT_PREFIX = "mgr_reject_"

CALLBACK_MGR_FEEDBACK_PREFIX = "mgr_feedback_status_"
CALLBACK_START_CANDIDATE_FEEDBACK = "start_candidate_feedback"

CALLBACK_ONBOARDING_FOLLOWUP_YES = "onboarding_followup_yes"
CALLBACK_ONBOARDING_FOLLOWUP_NO = "onboarding_followup_no"

CALLBACK_GO_TO_MAIN_MENU = "go_to_main_menu"