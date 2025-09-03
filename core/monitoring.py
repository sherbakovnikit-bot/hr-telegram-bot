import asyncio
import logging
import time
import os
from datetime import datetime

from aiohttp import web
from telegram.ext import Application

from core import settings

logger = logging.getLogger(__name__)


async def handle_http_ping(request: web.Request) -> web.Response:
    try:
        with open(settings.PING_FILE, 'w') as f:
            f.write(str(time.time()))
        logger.info("HTTP PING HANDLED, ping.txt updated.")
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"HTTP PING: Failed to write ping file: {e}", exc_info=True)
        return web.Response(text="ERROR", status=500)


async def start_http_server(app: web.Application, stop_event: asyncio.Event):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8888)
    try:
        await site.start()
        logger.info("HTTP Health Check Server started at http://0.0.0.0:8888")
        await stop_event.wait()
    except Exception as e:
        logger.error(f"HTTP server failed to start or run: {e}")
    finally:
        logger.info("Stopping HTTP Health Check Server...")
        await runner.cleanup()
        logger.info("HTTP server cleanup complete.")


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