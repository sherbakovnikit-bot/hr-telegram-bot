import asyncio
import sqlite3
import logging
import json
from typing import List, Tuple, Optional, Dict, Any

from core.settings import DATABASE_FILE

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    pass


def _execute_query_sync(query: str, params: tuple = (), fetch: Optional[str] = None):
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute(query, params)
            conn.commit()
            if fetch == "one":
                result = cursor.fetchone()
                return dict(result) if result else None
            if fetch == "all":
                results = cursor.fetchall()
                return [dict(row) for row in results]
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Database error on query '{query}': {e}", exc_info=True)
        raise DatabaseError(f"Database operation failed: {e}")


async def execute_query(query: str, params: tuple = (), fetch: Optional[str] = None):
    try:
        return await asyncio.to_thread(_execute_query_sync, query, params, fetch)
    except DatabaseError:
        return None if fetch else 0


async def init_db():
    query_managers = "CREATE TABLE IF NOT EXISTS managers (user_id INTEGER NOT NULL, restaurant_code TEXT NOT NULL, full_name TEXT, username TEXT, PRIMARY KEY (user_id, restaurant_code));"
    await execute_query(query_managers)
    query_directors = "CREATE TABLE IF NOT EXISTS directors (user_id INTEGER NOT NULL, restaurant_code TEXT NOT NULL, full_name TEXT, username TEXT, PRIMARY KEY (user_id, restaurant_code));"
    await execute_query(query_directors)
    query_pending_managers = "CREATE TABLE IF NOT EXISTS pending_managers (user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, username TEXT, restaurant_code TEXT NOT NULL, restaurant_name TEXT NOT NULL, request_time REAL NOT NULL);"
    await execute_query(query_pending_managers)
    query_pending_feedback = "CREATE TABLE IF NOT EXISTS pending_feedback (feedback_id TEXT PRIMARY KEY, manager_id INTEGER NOT NULL, message_id INTEGER, candidate_id INTEGER NOT NULL, candidate_name TEXT NOT NULL, job_data_json TEXT NOT NULL, created_at REAL NOT NULL);"
    await execute_query(query_pending_feedback)
    query_surveys = "CREATE TABLE IF NOT EXISTS surveys (survey_type TEXT NOT NULL, user_id INTEGER NOT NULL, restaurant_code TEXT, completed_at REAL NOT NULL, PRIMARY KEY (survey_type, user_id));"
    await execute_query(query_surveys)
    query_sheets_queue = "CREATE TABLE IF NOT EXISTS sheets_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, sheet_name TEXT NOT NULL, data_json TEXT NOT NULL, created_at REAL NOT NULL, attempts INTEGER DEFAULT 0, is_processed BOOLEAN DEFAULT 0);"
    await execute_query(query_sheets_queue)
    query_candidate_restaurants = "CREATE TABLE IF NOT EXISTS candidate_restaurants (user_id INTEGER PRIMARY KEY, restaurant_code TEXT NOT NULL);"
    await execute_query(query_candidate_restaurants)
    query_feedback_history = "CREATE TABLE IF NOT EXISTS feedback_history (feedback_id TEXT PRIMARY KEY, manager_id INTEGER NOT NULL, message_id INTEGER, candidate_id INTEGER NOT NULL, candidate_name TEXT NOT NULL, job_data_json TEXT NOT NULL, created_at REAL NOT NULL, decision_at REAL, decision_by_id INTEGER, status TEXT);"
    await execute_query(query_feedback_history)
    query_employees = "CREATE TABLE IF NOT EXISTS employees (user_id INTEGER PRIMARY KEY, full_name TEXT, restaurant_code TEXT, is_active BOOLEAN DEFAULT 1, added_at REAL);"
    await execute_query(query_employees)
    logger.info("Database initialized successfully.")


async def add_employee(user_id: int, full_name: str, restaurant_code: str):
    query = "INSERT OR REPLACE INTO employees (user_id, full_name, restaurant_code, is_active, added_at) VALUES (?, ?, ?, 1, ?)"
    await execute_query(query, (user_id, full_name, restaurant_code, asyncio.get_event_loop().time()))
    logger.info(f"Added/updated employee {user_id} ({full_name}) for restaurant {restaurant_code}.")


async def register_candidate(user_id: int, full_name: str, restaurant_code: str):
    query = "INSERT OR REPLACE INTO employees (user_id, full_name, restaurant_code, is_active, added_at) VALUES (?, ?, ?, 0, ?)"
    await execute_query(query, (user_id, full_name, restaurant_code, asyncio.get_event_loop().time()))
    logger.info(f"Registered candidate {user_id} ({full_name}) for restaurant {restaurant_code} as inactive.")


async def activate_employee(user_id: int):
    query = "UPDATE employees SET is_active = 1 WHERE user_id = ?"
    await execute_query(query, (user_id,))
    logger.info(f"Activated employee {user_id}.")


async def deactivate_employee(user_id: int):
    query = "UPDATE employees SET is_active = 0 WHERE user_id = ?"
    await execute_query(query, (user_id,))
    logger.info(f"Deactivated employee {user_id}.")


async def toggle_employee_status(user_id: int):
    query = "UPDATE employees SET is_active = NOT is_active WHERE user_id = ?"
    await execute_query(query, (user_id,))
    logger.info(f"Toggled active status for employee {user_id}.")


async def get_active_employees() -> List[int]:
    query = "SELECT user_id FROM employees WHERE is_active = 1"
    result = await execute_query(query, fetch="all")
    return [row['user_id'] for row in result] if result else []


async def get_all_employees_with_status() -> List[Dict[str, Any]]:
    query = "SELECT user_id, full_name, restaurant_code, is_active FROM employees ORDER BY is_active DESC, full_name"
    return await execute_query(query, fetch="all") or []


async def count_employees_in_restaurant(restaurant_code: str) -> int:
    query = "SELECT COUNT(user_id) as count FROM employees WHERE restaurant_code = ?"
    result = await execute_query(query, (restaurant_code,), fetch="one")
    return result['count'] if result else 0


async def get_employees_paginated(restaurant_code: str, page: int, limit: int) -> List[Dict[str, Any]]:
    offset = page * limit
    query = "SELECT user_id, full_name, is_active FROM employees WHERE restaurant_code = ? ORDER BY is_active DESC, full_name LIMIT ? OFFSET ?"
    return await execute_query(query, (restaurant_code, limit, offset), fetch="all") or []


async def get_feedback_from_history(feedback_id: str) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM feedback_history WHERE feedback_id = ?"
    result = await execute_query(query, (feedback_id,), fetch="one")
    if result:
        result['job_data'] = json.loads(result['job_data_json'])
        return result
    return None


async def move_pending_feedback_to_history(candidate_id: int, decision_by_id: int, status: str):
    first_task_details_query = "SELECT * FROM pending_feedback WHERE candidate_id = ? LIMIT 1"
    task_details = await execute_query(first_task_details_query, (candidate_id,), fetch="one")
    if not task_details:
        await remove_all_pending_feedback_for_candidate(candidate_id)
        return

    insert_query = "INSERT OR IGNORE INTO feedback_history (feedback_id, manager_id, message_id, candidate_id, candidate_name, job_data_json, created_at, decision_at, decision_by_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    decision_time = asyncio.get_event_loop().time()
    await execute_query(insert_query, (
        task_details['feedback_id'], task_details['manager_id'], task_details['message_id'],
        task_details['candidate_id'], task_details['candidate_name'], task_details['job_data_json'],
        task_details['created_at'], decision_time, decision_by_id, status
    ))
    logger.info(f"Moved feedback for candidate {candidate_id} to history with status '{status}'.")
    await remove_all_pending_feedback_for_candidate(candidate_id)


async def add_to_sheets_db_queue(sheet_name: str, data: list):
    query = "INSERT INTO sheets_queue (sheet_name, data_json, created_at) VALUES (?, ?, ?)"
    data_json = json.dumps(data, ensure_ascii=False)
    created_at = asyncio.get_event_loop().time()
    await execute_query(query, (sheet_name, data_json, created_at))


async def get_sheets_queue_batch(limit: int = 50) -> List[Dict[str, Any]]:
    query = "SELECT id, sheet_name, data_json, attempts FROM sheets_queue WHERE is_processed = 0 ORDER BY created_at LIMIT ?"
    result = await execute_query(query, (limit,), fetch="all")
    return result if result else []


async def mark_sheets_queue_items_processed(item_ids: List[int]):
    if not item_ids: return
    query = f"UPDATE sheets_queue SET is_processed = 1 WHERE id IN ({','.join(['?'] * len(item_ids))})"
    await execute_query(query, tuple(item_ids))


async def increment_sheets_queue_attempts(item_ids: List[int]):
    if not item_ids: return
    query = f"UPDATE sheets_queue SET attempts = attempts + 1 WHERE id IN ({','.join(['?'] * len(item_ids))})"
    await execute_query(query, tuple(item_ids))


async def add_manager(user_id: int, restaurant_code: str, full_name: str, username: Optional[str]):
    query = "INSERT OR REPLACE INTO managers (user_id, restaurant_code, full_name, username) VALUES (?, ?, ?, ?)"
    await execute_query(query, (user_id, restaurant_code, full_name, username))
    logger.info(f"Added/updated manager {user_id} ({full_name}) for restaurant {restaurant_code}.")


async def get_manager_details(user_id: int) -> Optional[Dict[str, Any]]:
    query = "SELECT user_id, full_name, username FROM managers WHERE user_id = ? LIMIT 1"
    return await execute_query(query, (user_id,), fetch="one")


async def remove_manager_from_all_restaurants(user_id: int):
    query = "DELETE FROM managers WHERE user_id = ?"
    await execute_query(query, (user_id,))
    logger.info(f"Removed manager {user_id} from all restaurants.")


async def get_managers_for_restaurant(restaurant_code: str) -> List[int]:
    query = "SELECT user_id FROM managers WHERE restaurant_code = ?"
    result = await execute_query(query, (restaurant_code,), fetch="all")
    return [row['user_id'] for row in result] if result else []


async def get_all_managers_by_restaurant() -> dict[str, list[dict]]:
    query = "SELECT restaurant_code, user_id, full_name, username FROM managers ORDER BY restaurant_code, full_name"
    result = await execute_query(query, fetch="all")
    managers_map = {}
    if result:
        for row in result:
            res_code = row['restaurant_code']
            if res_code not in managers_map:
                managers_map[res_code] = []
            managers_map[res_code].append(
                {'user_id': row['user_id'], 'full_name': row['full_name'], 'username': row['username']}
            )
    return managers_map


async def is_manager_in_restaurant(user_id: int, restaurant_code: str) -> bool:
    query = "SELECT 1 FROM managers WHERE user_id = ? AND restaurant_code = ?"
    result = await execute_query(query, (user_id, restaurant_code), fetch="one")
    return result is not None


async def is_user_a_manager(user_id: int) -> bool:
    query = "SELECT 1 FROM managers WHERE user_id = ? LIMIT 1"
    result = await execute_query(query, (user_id,), fetch="one")
    return result is not None


async def add_pending_manager(user_id: int, restaurant_code: str, restaurant_name: str, full_name: str, username: Optional[str],
                              request_time: float):
    query = "INSERT OR REPLACE INTO pending_managers (user_id, restaurant_code, restaurant_name, full_name, username, request_time) VALUES (?, ?, ?, ?, ?, ?)"
    await execute_query(query, (user_id, restaurant_code, restaurant_name, full_name, username, request_time))
    logger.info(f"Added pending manager request for user {user_id}.")


async def get_pending_manager(user_id: int) -> Optional[dict]:
    query = "SELECT restaurant_code, restaurant_name, full_name, username FROM pending_managers WHERE user_id = ?"
    result = await execute_query(query, (user_id,), fetch="one")
    return result


async def remove_pending_manager(user_id: int):
    query = "DELETE FROM pending_managers WHERE user_id = ?"
    await execute_query(query, (user_id,))
    logger.info(f"Removed pending manager request for user {user_id}.")


async def add_pending_feedback(feedback_id: str, manager_id: int, message_id: int, candidate_id: int,
                               candidate_name: str,
                               job_data: dict, created_at: float):
    query = "INSERT INTO pending_feedback (feedback_id, manager_id, message_id, candidate_id, candidate_name, job_data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    job_data_json = json.dumps(job_data, ensure_ascii=False)
    await execute_query(query,
                        (feedback_id, manager_id, message_id, candidate_id, candidate_name, job_data_json, created_at))
    logger.info(f"Added pending feedback task {feedback_id} for manager {manager_id} about candidate {candidate_id}.")


async def get_pending_feedback_for_manager(manager_id: int) -> List[Dict[str, Any]]:
    query = "SELECT feedback_id, candidate_name FROM pending_feedback WHERE manager_id = ?"
    result = await execute_query(query, (manager_id,), fetch="all")
    return [{"id": row['feedback_id'], "name": row['candidate_name']} for row in result] if result else []


async def get_all_pending_feedback() -> List[Dict[str, Any]]:
    query = "SELECT feedback_id, candidate_id, candidate_name, job_data_json FROM pending_feedback ORDER BY created_at"
    results = await execute_query(query, fetch="all")
    if not results: return []
    tasks, processed_candidates = [], set()
    for row in results:
        candidate_id = row['candidate_id']
        if candidate_id in processed_candidates: continue
        job_data = json.loads(row['job_data_json'])
        tasks.append({
            "id": row['feedback_id'],
            "candidate_id": candidate_id,
            "name": row['candidate_name'],
            "restaurant_name": job_data.get('interview_restaurant_name', 'Неизвестно')
        })
        processed_candidates.add(candidate_id)
    return tasks


async def get_pending_feedback_by_id(feedback_id: str) -> Optional[Dict[str, Any]]:
    query = "SELECT * FROM pending_feedback WHERE feedback_id = ?"
    result = await execute_query(query, (feedback_id,), fetch="one")
    if result:
        result['job_data'] = json.loads(result['job_data_json'])
        return result
    return None


async def get_candidate_id_from_feedback_id(feedback_id: str) -> Optional[int]:
    query = "SELECT candidate_id FROM pending_feedback WHERE feedback_id = ? LIMIT 1"
    result = await execute_query(query, (feedback_id,), fetch="one")
    return result['candidate_id'] if result else None


async def get_all_pending_feedback_for_candidate(candidate_id: int) -> List[Dict[str, Any]]:
    query = "SELECT * FROM pending_feedback WHERE candidate_id = ?"
    return await execute_query(query, (candidate_id,), fetch="all") or []


async def remove_all_pending_feedback_for_candidate(candidate_id: int):
    query = "DELETE FROM pending_feedback WHERE candidate_id = ?"
    await execute_query(query, (candidate_id,))
    logger.info(f"Removed all pending feedback tasks for candidate {candidate_id}.")


async def get_survey_counts_by_restaurant() -> Dict[str, Dict[str, int]]:
    query = "SELECT restaurant_code, survey_type, COUNT(*) as count FROM surveys GROUP BY restaurant_code, survey_type"
    results = await execute_query(query, fetch="all")
    stats = {}
    if not results: return stats
    for row in results:
        res_code, survey_type, count = row['restaurant_code'] or 'N/A', row['survey_type'], row['count']
        if res_code not in stats: stats[res_code] = {}
        stats[res_code][survey_type] = count
    return stats


async def log_survey_completion(survey_type: str, user_id: int, restaurant_code: Optional[str] = None):
    query = "INSERT OR REPLACE INTO surveys (survey_type, user_id, restaurant_code, completed_at) VALUES (?, ?, ?, ?)"
    await execute_query(query, (survey_type, user_id, restaurant_code, asyncio.get_event_loop().time()))


async def is_survey_completed(survey_type: str, user_id: int) -> bool:
    query = "SELECT 1 FROM surveys WHERE survey_type = ? AND user_id = ?"
    result = await execute_query(query, (survey_type, user_id), fetch="one")
    return result is not None


async def log_candidate_restaurant(user_id: int, restaurant_code: str):
    query = "INSERT OR REPLACE INTO candidate_restaurants (user_id, restaurant_code) VALUES (?, ?)"
    await execute_query(query, (user_id, restaurant_code))


async def get_candidate_restaurant(user_id: int) -> Optional[str]:
    query = "SELECT restaurant_code FROM candidate_restaurants WHERE user_id = ?"
    result = await execute_query(query, (user_id,), fetch="one")
    return result['restaurant_code'] if result else None


async def delete_user_data(user_id: int):
    logger.warning(f"Deleting all data for user_id: {user_id}")
    tables_with_user_id = ["managers", "directors", "pending_managers", "surveys", "candidate_restaurants", "employees"]
    for table in tables_with_user_id:
        await execute_query(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    await execute_query("DELETE FROM pending_feedback WHERE candidate_id = ?", (user_id,))
    await execute_query("DELETE FROM pending_feedback WHERE manager_id = ?", (user_id,))
    await execute_query("UPDATE feedback_history SET decision_by_id = NULL WHERE decision_by_id = ?", (user_id,))
    logger.info(f"Successfully deleted data for user_id: {user_id}")


async def remove_manager(user_id: int, restaurant_code: str):
    query = "DELETE FROM managers WHERE user_id = ? AND restaurant_code = ?"
    await execute_query(query, (user_id, restaurant_code))
    logger.info(f"Removed manager {user_id} from restaurant {restaurant_code}.")


async def add_director(user_id: int, restaurant_code: str, full_name: str, username: Optional[str]):
    query = "INSERT OR REPLACE INTO directors (user_id, restaurant_code, full_name, username) VALUES (?, ?, ?, ?)"
    await execute_query(query, (user_id, restaurant_code, full_name, username))
    logger.info(f"Added/updated director {user_id} ({full_name}) for restaurant {restaurant_code}.")


async def remove_director(user_id: int, restaurant_code: str):
    query = "DELETE FROM directors WHERE user_id = ? AND restaurant_code = ?"
    await execute_query(query, (user_id, restaurant_code))
    logger.info(f"Removed director {user_id} from restaurant {restaurant_code}.")


async def get_directors_for_restaurant(restaurant_code: str) -> List[int]:
    query = "SELECT user_id FROM directors WHERE restaurant_code = ?"
    result = await execute_query(query, (restaurant_code,), fetch="all")
    return [row['user_id'] for row in result] if result else []


async def get_all_directors_by_restaurant() -> dict[str, list[dict]]:
    query = "SELECT restaurant_code, user_id, full_name, username FROM directors ORDER BY restaurant_code, full_name"
    result = await execute_query(query, fetch="all")
    directors_map = {}
    if result:
        for row in result:
            res_code = row['restaurant_code']
            if res_code not in directors_map:
                directors_map[res_code] = []
            directors_map[res_code].append(
                {'user_id': row['user_id'], 'full_name': row['full_name'], 'username': row['username']}
            )
    return directors_map


async def is_director_in_restaurant(user_id: int, restaurant_code: str) -> bool:
    query = "SELECT 1 FROM directors WHERE user_id = ? AND restaurant_code = ?"
    result = await execute_query(query, (user_id, restaurant_code), fetch="one")
    return result is not None


async def is_user_a_director(user_id: int) -> bool:
    query = "SELECT 1 FROM directors WHERE user_id = ? LIMIT 1"
    result = await execute_query(query, (user_id,), fetch="one")
    return result is not None


async def get_director_restaurants(user_id: int) -> List[Dict[str, str]]:
    query = "SELECT restaurant_code FROM directors WHERE user_id = ?"
    results = await execute_query(query, (user_id,), fetch="all")
    if not results:
        return []

    res_list = []
    for row in results:
        res_code_suffix = row['restaurant_code']
        res_name = next((name for name, code in RESTAURANT_OPTIONS if code.endswith(res_code_suffix)), res_code_suffix)
        res_list.append({"code": res_code_suffix, "name": res_name})
    return res_list


async def get_candidate_funnel_stats() -> Dict[str, Dict[str, int]]:
    pending_query = """
        SELECT jd.value ->> 'interview_restaurant_name' as restaurant_name
        FROM pending_feedback, json_each(job_data_json) as jd
        WHERE jd.key = 'interview_restaurant_name'
    """

    history_query = """
        SELECT
            jd.value ->> 'interview_restaurant_name' as restaurant_name,
            status
        FROM feedback_history, json_each(job_data_json) as jd
        WHERE jd.key = 'interview_restaurant_name'
    """

    pending_results = await execute_query(pending_query, fetch="all")
    history_results = await execute_query(history_query, fetch="all")

    stats = defaultdict(lambda: defaultdict(int))

    if pending_results:
        processed_candidates = set()
        pending_unique_query = "SELECT DISTINCT candidate_id, job_data_json FROM pending_feedback"
        pending_unique_results = await execute_query(pending_unique_query, fetch="all")
        if pending_unique_results:
            for row in pending_unique_results:
                job_data = json.loads(row['job_data_json'])
                res_name = job_data.get('interview_restaurant_name', 'Неизвестно')
                stats[res_name]['На рассмотрении'] += 1

    if history_results:
        for row in history_results:
            res_name = row.get('restaurant_name', 'Неизвестно')
            status = row.get('status', 'Другое')

            if 'Ознакомительная смена' in status:
                stats[res_name]['Ознакомительная смена'] += 1
            elif 'отказался' in status.lower() or 'не подходит' in status.lower() or 'удалено' in status.lower():
                stats[res_name]['Отказ/Не подходит'] += 1
            elif 'подумает' in status.lower():
                stats[res_name]['Думает'] += 1
            else:
                stats[res_name][status] += 1

    return dict(stats)

async def get_salary_expectations_stats() -> Dict[str, Dict[str, int]]:
    query = """
        SELECT
            json_extract(data_json, '$[1]') as restaurant_name,
            json_extract(data_json, '$[27]') as expected_income,
            COUNT(*) as count
        FROM sheets_queue
        WHERE sheet_name = 'Первичный контакт' AND is_processed = 1
        GROUP BY restaurant_name, expected_income
    """
    results = await execute_query(query, fetch="all")
    if not results:
        return {}

    stats = defaultdict(lambda: defaultdict(int))
    for row in results:
        res_name = row['restaurant_name'] if row['restaurant_name'] else 'Неизвестно'
        income_level = row['expected_income'] if row['expected_income'] else 'Не указано'
        stats[res_name][income_level] += row['count']

    return dict(stats)

async def get_exit_interview_stats() -> List[Dict[str, Any]]:
    query = """
        SELECT
            json_extract(data_json, '$[2]') as restaurant_name,
            json_extract(data_json, '$[5]') as reason,
            json_extract(data_json, '$[7]') as leadership_rating,
            json_extract(data_json, '$[8]') as training_rating,
            json_extract(data_json, '$[9]') as feedback_freq
        FROM sheets_queue
        WHERE sheet_name = 'exit interview' AND is_processed = 1
    """
    results = await execute_query(query, fetch="all")
    return results if results else []

async def get_recruitment_sources_stats() -> List[Dict[str, Any]]:
    query = """
        SELECT
            json_extract(data_json, '$[1]') as restaurant_name,
            json_extract(data_json, '$[5]') as source,
            COUNT(*) as count
        FROM sheets_queue
        WHERE sheet_name = 'Первичный контакт' AND is_processed = 1
        GROUP BY restaurant_name, source
    """
    results = await execute_query(query, fetch="all")
    return results if results else []

async def get_recruitment_experience_stats() -> List[Dict[str, Any]]:
    query = """
        SELECT
            json_extract(data_json, '$[1]') as restaurant_name,
            json_extract(data_json, '$[25]') as experience,
            COUNT(*) as count
        FROM sheets_queue
        WHERE sheet_name = 'Первичный контакт' AND is_processed = 1
        GROUP BY restaurant_name, experience
    """
    results = await execute_query(query, fetch="all")
    return results if results else []

async def get_climate_survey_stats() -> List[Dict[str, Any]]:
    query = """
        SELECT
            json_extract(data_json, '$[2]') as restaurant_name,
            json_extract(data_json, '$[4]') as recommend,
            json_extract(data_json, '$[7]') as expectations,
            json_extract(data_json, '$[8]') as best_ability,
            json_extract(data_json, '$[9]') as praise,
            json_extract(data_json, '$[10]') as development_care,
            json_extract(data_json, '$[11]') as opinion,
            json_extract(data_json, '$[12]') as colleague_success,
            json_extract(data_json, '$[13]') as mission,
            json_extract(data_json, '$[14]') as importance,
            json_extract(data_json, '$[15]') as growth_opportunity,
            json_extract(data_json, '$[16]') as support,
            json_extract(data_json, '$[17]') as friends,
            json_extract(data_json, '$[18]') as team_part,
            json_extract(data_json, '$[5]') as recommend_reason
        FROM sheets_queue
        WHERE sheet_name = 'Замер климата' AND is_processed = 1
    """
    results = await execute_query(query, fetch="all")
    return results if results else []

async def get_climate_stats_by_position() -> List[Dict[str, Any]]:
    query = """
        SELECT
            json_extract(data_json, '$[3]') as position,
            json_extract(data_json, '$[4]') as recommend,
            COUNT(*) as count
        FROM sheets_queue
        WHERE sheet_name = 'Замер климата' AND is_processed = 1
        GROUP BY position, recommend
    """
    results = await execute_query(query, fetch="all")
    return results if results else []