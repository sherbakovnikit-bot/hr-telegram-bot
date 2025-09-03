import os
import time
import psutil
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent.resolve()))
from core.logging_config import setup_logging

BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

logger = setup_logging("MONITOR")

FROZEN_THRESHOLD_SECONDS = 90
CHECK_INTERVAL_SECONDS = 60

PID_FILE = BASE_DIR / "bot.pid"
HEARTBEAT_FILE = BASE_DIR / "heartbeat.txt"
PING_FILE = BASE_DIR / "ping.txt"

def get_bot_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None

def kill_process(pid: int):
    try:
        process = psutil.Process(pid)
        logger.warning(f"Process {pid} found. Attempting to terminate gracefully.")
        process.terminate()
        try:
            process.wait(timeout=5)
            logger.info(f"Process {pid} terminated gracefully.")
        except psutil.TimeoutExpired:
            logger.warning(f"Graceful termination failed. Forcing kill on process {pid}.")
            process.kill()
            logger.info(f"Process {pid} killed.")
    except psutil.NoSuchProcess:
        logger.warning(f"Process {pid} not found (already terminated).")
    except psutil.Error as e:
        logger.error(f"Failed to terminate process {pid} with psutil error: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred while terminating process {pid}: {e}", exc_info=True)


def cleanup_files():
    for f in [PID_FILE, HEARTBEAT_FILE, PING_FILE]:
        if f.exists():
            try:
                os.remove(f)
            except (OSError, PermissionError) as e:
                logger.error(f"Failed to remove file {f}: {e}")
    logger.info("State files cleaned up.")

def send_ping_to_bot():
    url = "http://localhost:8888/ping"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        logger.info(f"HTTP Ping to {url} successful (status {response.status_code}).")
        return True
    except requests.RequestException as e:
        logger.error(f"Network error sending HTTP ping to {url}: {e}")
        return False

if __name__ == "__main__":
    logger.info("Health Monitor started.")
    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)

            bot_pid = get_bot_pid()
            if not bot_pid or not psutil.pid_exists(bot_pid):
                logger.warning(f"Bot is not running (PID {bot_pid} not found). Skipping check.")
                continue

            if HEARTBEAT_FILE.exists():
                try:
                    time_since_heartbeat = time.time() - HEARTBEAT_FILE.stat().st_mtime
                    if time_since_heartbeat > FROZEN_THRESHOLD_SECONDS:
                        logger.critical(
                            f"PROCESS HEARTBEAT LOST! Last signal was {time_since_heartbeat:.0f}s ago. Killing process {bot_pid}."
                        )
                        kill_process(bot_pid)
                        cleanup_files()
                        continue
                except FileNotFoundError:
                    logger.warning("Heartbeat file disappeared during check. Race condition?")
            else:
                 logger.warning("Heartbeat file (heartbeat.txt) not found. Bot might have just started.")

            logger.info("Performing functional check via HTTP /ping...")
            if not send_ping_to_bot():
                logger.critical(f"BOT IS UNRESPONSIVE! HTTP server did not respond. Killing process {bot_pid}.")
                kill_process(bot_pid)
                cleanup_files()
            else:
                logger.info("✅ HEALTH CHECK PASSED. Bot is responsive.")

        except KeyboardInterrupt:
            logger.info("Health Monitor stopped by user.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Unexpected error in monitor loop: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL_SECONDS)