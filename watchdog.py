import subprocess
import time
import sys
import os
from pathlib import Path

sys.path.append(str(Path(__file__).parent.resolve()))
from core.logging_config import setup_logging

logger = setup_logging("WATCHDOG")

script_dir = Path(__file__).parent.resolve()
main_script_path = script_dir / "main.py"

if not main_script_path.is_file():
    logger.critical(f"Main bot script not found at: {main_script_path}")
    sys.exit(1)

python_executable = sys.executable

RESTART_INTERVAL_SECONDS = 604800

logger.info(f"Using Python: {python_executable}")
logger.info(f"Path to bot script: {main_script_path}")
logger.info(f"Watchdog starting. Bot will be restarted every {RESTART_INTERVAL_SECONDS / 3600:.1f} hours.")

while True:
    try:
        logger.info("Starting bot process...")
        start_time = time.time()

        process = subprocess.Popen(
            [python_executable, str(main_script_path)],
            env=os.environ.copy()
        )

        while True:
            return_code = process.poll()
            if return_code is not None:
                logger.warning(f"Bot process exited with code: {return_code}. Restarting in 5 seconds...")
                time.sleep(5)
                break

            uptime = time.time() - start_time
            if uptime > RESTART_INTERVAL_SECONDS:
                logger.info(f"Planned restart: bot has been running for {uptime:.0f} seconds. Terminating...")
                process.terminate()
                try:
                    process.wait(timeout=10)
                    logger.info("Bot terminated gracefully for planned restart.")
                except subprocess.TimeoutExpired:
                    logger.warning("Graceful termination failed during planned restart. Forcing kill.")
                    process.kill()
                break

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Shutting down watchdog...")
        if 'process' in locals() and process.poll() is None:
            logger.info("Terminating bot process...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Bot did not terminate gracefully, killing.")
                process.kill()
        break

    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Error in watchdog loop regarding subprocess: {e}", exc_info=True)
        logger.warning("Restarting in 15 seconds after error...")
        time.sleep(15)
        continue
    except Exception as e:
        logger.error(f"Unexpected error in watchdog loop: {e}", exc_info=True)
        logger.warning("Restarting in 15 seconds after error...")
        time.sleep(15)
        continue

    time.sleep(1)

logger.info("Watchdog has been shut down.")