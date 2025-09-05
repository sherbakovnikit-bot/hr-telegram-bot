import asyncio
import logging
import time
import os
from datetime import datetime

from telegram.ext import Application

from core import settings

logger = logging.getLogger(__name__)


async def heartbeat_task(application: Application, stop_event: asyncio.Event, bot_data):
    while not stop_event.is_set():
        try:
            now = time.time()
            with open(settings.HEARTBEAT_FILE, "w") as f:
                f.write(str(now))

            await asyncio.wait_for(stop_event.wait(), timeout=settings.HEARTBEAT_INTERVAL_SECONDS)

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in heartbeat task: {e}", exc_info=True)
            await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)

    logger.info("Heartbeat task finished.")
    if os.path.exists(settings.HEARTBEAT_FILE):
        try:
            os.remove(settings.HEARTBEAT_FILE)
        except OSError:
            pass