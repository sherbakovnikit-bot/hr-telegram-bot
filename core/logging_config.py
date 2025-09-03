import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "bot.log"


class InteractionAndErrorFilter(logging.Filter):
    def filter(self, record):
        is_interaction = record.name.startswith('handlers') or record.name == '__main__'
        is_error_level = record.levelno >= logging.WARNING
        return is_interaction or is_error_level


def setup_logging(name: str):
    return logging.getLogger(name)


formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log_filter = InteractionAndErrorFilter()

file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when='W0',
    interval=1,
    backupCount=1,
    encoding='utf-8',
    delay=False
)
file_handler.setFormatter(formatter)
file_handler.addFilter(log_filter)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
stream_handler.addFilter(log_filter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

if root_logger.hasHandlers():
    root_logger.handlers.clear()

root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)