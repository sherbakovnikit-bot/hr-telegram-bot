import logging
import time
import os
from telegram.ext import ContextTypes
from core import settings

logger = logging.getLogger(__name__)

async def heartbeat_task(context: ContextTypes.DEFAULT_TYPE):
    """
    Периодическая задача, вызываемая JobQueue для обновления файла heartbeat.
    Это показывает, что event loop бота жив и обрабатывает задачи.
    """
    try:
        now = time.time()
        with open(settings.HEARTBEAT_FILE, "w") as f:
            f.write(str(now))
    except Exception as e:
        logger.error(f"Error in heartbeat_task: {e}", exc_info=True)