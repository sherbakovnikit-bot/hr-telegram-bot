import asyncio
import gspread
import gspread_asyncio
import html
import logging
import requests
import json
from collections import defaultdict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import List, Any

from google.oauth2.service_account import Credentials
from telegram.ext import Application
from telegram.constants import ParseMode

from core import settings, database

logger = logging.getLogger(__name__)

GSPREAD_RETRY_ERRORS = (
    gspread.exceptions.APIError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.RequestException,
    gspread.exceptions.GSpreadException,
    TimeoutError,
)

MAX_WRITE_ATTEMPTS = 3

retry_gspread_operation = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=10, max=60),
    retry=retry_if_exception_type(GSPREAD_RETRY_ERRORS),
    reraise=True,
    before_sleep=lambda retry_state: logger.warning(
        f"Retrying GSheets operation (attempt {retry_state.attempt_number}) due to: {retry_state.outcome.exception()}"
    )
)


async def init_google_sheets_client() -> gspread_asyncio.AsyncioGspreadClientManager | None:
    logger.info("Initializing Google Sheets client...")
    if not settings.GOOGLE_CREDENTIALS_JSON:
        logger.error("GSheets credentials not found in GOOGLE_CREDENTIALS_JSON env variable. Sheets will not work.")
        return None
    if not settings.SPREADSHEET_ID:
        logger.error("SPREADSHEET_ID is not set in .env file. Sheets will not work.")
        return None

    try:
        creds_json = json.loads(settings.GOOGLE_CREDENTIALS_JSON)

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = Credentials.from_service_account_info(creds_json, scopes=scope)

        agc_manager = gspread_asyncio.AsyncioGspreadClientManager(lambda: creds)
        client = await agc_manager.authorize()
        await client.open_by_key(settings.SPREADSHEET_ID)
        logger.info("Google Sheets client initialized and spreadsheet access verified.")
        return agc_manager
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GOOGLE_CREDENTIALS_JSON. It might be malformed. Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error initializing Google Sheets client: {e}", exc_info=True)
    return None


@retry_gspread_operation
async def append_rows_to_sheet(worksheet: gspread_asyncio.AsyncioGspreadWorksheet, data: List[List[Any]]):
    if not data:
        return
    await worksheet.append_rows(data, value_input_option='USER_ENTERED')
    logger.info(f"Successfully appended {len(data)} rows to sheet '{worksheet.title}'.")


async def process_batch_for_sheet(application: Application, agc_manager, sheet_name: str, items: List[dict]):
    item_ids = [item['id'] for item in items]
    data_to_write = [json.loads(item['data_json']) for item in items]

    try:
        agc = await agc_manager.authorize()
        spreadsheet = await agc.open_by_key(settings.SPREADSHEET_ID)
        worksheet = await spreadsheet.worksheet(sheet_name)
        await append_rows_to_sheet(worksheet, data_to_write)
        await database.mark_sheets_queue_items_processed(item_ids)
    except Exception as e:
        logger.error(f"Failed to write batch to '{sheet_name}': {e}. Incrementing attempts.", exc_info=True)
        await database.increment_sheets_queue_attempts(item_ids)

        failed_items_count = 0
        for item in items:
            if item['attempts'] + 1 >= MAX_WRITE_ATTEMPTS:
                failed_items_count += 1

        if failed_items_count > 0:
            message = (f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА GOOGLE SHEETS</b> 🚨\n"
                       f"Не удалось записать данные в лист <b>'{html.escape(sheet_name)}'</b> после {MAX_WRITE_ATTEMPTS} попыток.\n"
                       f"<b>Ошибка:</b> <pre>{html.escape(str(e))[:1000]}</pre>\n"
                       f"❗️ <b>{failed_items_count} записей помечены как 'неудачные' и больше не будут обрабатываться.</b> Проверьте БД и логи.")

            if settings.ADMIN_IDS:
                for admin_id in settings.ADMIN_IDS:
                    try:
                        await application.bot.send_message(admin_id, message, parse_mode=ParseMode.HTML)
                    except Exception as notify_err:
                        logger.error(f"Failed to notify admin {admin_id}: {notify_err}")


async def batch_writer_task(application: Application, stop_event: asyncio.Event, agc_manager, bot_data):
    logger.info("Batch writer task started.")

    async def perform_write():
        try:
            batch = await database.get_sheets_queue_batch()
            if not batch:
                return

            logger.info(f"Found {len(batch)} items in queue to write to Google Sheets.")
            items_by_sheet = defaultdict(list)
            for item in batch:
                items_by_sheet[item['sheet_name']].append(item)

            for sheet_name, items in items_by_sheet.items():
                await process_batch_for_sheet(application, agc_manager, sheet_name, items)
        except Exception as e:
            logger.error(f"Unhandled exception in perform_write cycle: {e}", exc_info=True)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.BATCH_INTERVAL)
        except asyncio.TimeoutError:
            await perform_write()
        except asyncio.CancelledError:
            break

    logger.info("Batch writer task stopping. Performing final write...")
    await perform_write()
    logger.info("Batch writer task finished.")