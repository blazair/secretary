"""
repo.py
=======
The seam between SQLite and the pure planning modules.

Rows go in, Task dataclasses come out. Below this file nothing knows that
users, HTTP or SQLite exist, which is what lets scheduler.py, calibration.py
and capacity.py stay unchanged from the desktop build.

Every function takes user_id as its first argument, so an omitted filter is
visible at the call site rather than buried in a WHERE clause.
"""

from __future__ import annotations

import json

from db import get_db
from defaults import DEFAULT_SETTINGS
from models import Task, now_as_text

# Columns the API may write directly to a task.
EDITABLE_TASK_FIELDS = frozenset({
    "title", "estimated_minutes", "energy_level", "priority", "category",
    "due_date", "scheduled_date", "start_time", "note",
    "is_splittable", "min_session_minutes", "max_session_minutes",
})


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def _row_to_task(row) -> Task:
    return Task(
        task_id=str(row["id"]),
        title=row["title"],
        estimated_minutes=row["estimated_minutes"],
        energy_level=row["energy_level"],
        priority=row["priority"],
        category=row["category"],
        due_date=row["due_date"],
        scheduled_date=row["scheduled_date"],
        start_time=row["start_time"],
        created_at=row["created_at"],
        is_done=bool(row["is_done"]),
        completed_at=row["completed_at"],
        actual_minutes=row["actual_minutes"],
        times_deferred=row["times_deferred"],
        note=row["note"],
        is_splittable=bool(row["is_splittable"]),
        min_session_minutes=row["min_session_minutes"],
        max_session_minutes=row["max_session_minutes"],
    )


def load_tasks(user_id: int, include_done: bool = True) -> list[Task]:
    """Every live task for one user, as dataclasses the planner understands."""
    query = "SELECT * FROM tasks WHERE user_id = ? AND deleted_at IS NULL"
    if not include_done:
        query += " AND is_done = 0"
    query += " ORDER BY created_at, id"
    rows = get_db().execute(query, (user_id,)).fetchall()
    return [_row_to_task(row) for row in rows]


def get_task(user_id: int, task_id: int) -> Task | None:
    row = get_db().execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (task_id, user_id),
    ).fetchone()
    return _row_to_task(row) if row else None


def insert_task(user_id: int, task: Task) -> Task:
    """Stores a freshly parsed Task and returns it with its real id."""
    database = get_db()
    cursor = database.execute(
        """
        INSERT INTO tasks (
            user_id, title, estimated_minutes, energy_level, priority, category,
            due_date, scheduled_date, start_time, created_at, note,
            is_splittable, min_session_minutes, max_session_minutes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, task.title, task.estimated_minutes, task.energy_level,
            task.priority, task.category, task.due_date, task.scheduled_date,
            task.start_time, task.created_at or now_as_text(), task.note,
            1 if task.is_splittable else 0, task.min_session_minutes,
            task.max_session_minutes,
        ),
    )
    database.commit()
    task.task_id = str(cursor.lastrowid)
    return task


def update_task(user_id: int, task_id: int, changes: dict) -> Task | None:
    """Writes whitelisted fields. Returns the updated task, or None if absent."""
    allowed = {k: v for k, v in changes.items() if k in EDITABLE_TASK_FIELDS}
    if not allowed:
        return get_task(user_id, task_id)

    if "is_splittable" in allowed:
        allowed["is_splittable"] = 1 if allowed["is_splittable"] else 0

    assignments = ", ".join(f"{name} = ?" for name in allowed)
    database = get_db()
    database.execute(
        f"UPDATE tasks SET {assignments}, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (*allowed.values(), task_id, user_id),
    )
    database.commit()
    return get_task(user_id, task_id)


def set_task_done(user_id: int, task_id: int, actual_minutes: int | None) -> Task | None:
    database = get_db()
    database.execute(
        "UPDATE tasks SET is_done = 1, completed_at = ?, actual_minutes = ?, "
        "updated_at = datetime('now') WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (now_as_text(), actual_minutes, task_id, user_id),
    )
    database.commit()
    return get_task(user_id, task_id)


def reopen_task(user_id: int, task_id: int) -> Task | None:
    database = get_db()
    database.execute(
        "UPDATE tasks SET is_done = 0, completed_at = NULL, actual_minutes = NULL, "
        "updated_at = datetime('now') WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    )
    database.commit()
    return get_task(user_id, task_id)


def defer_task(user_id: int, task_id: int, new_day: str) -> Task | None:
    database = get_db()
    database.execute(
        "UPDATE tasks SET scheduled_date = ?, times_deferred = times_deferred + 1, "
        "updated_at = datetime('now') WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (new_day, task_id, user_id),
    )
    database.commit()
    return get_task(user_id, task_id)


def soft_delete_task(user_id: int, task_id: int) -> bool:
    database = get_db()
    cursor = database.execute(
        "UPDATE tasks SET deleted_at = datetime('now') "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (task_id, user_id),
    )
    database.commit()
    return cursor.rowcount > 0


def restore_task(user_id: int, task_id: int) -> Task | None:
    database = get_db()
    database.execute(
        "UPDATE tasks SET deleted_at = NULL, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    )
    database.commit()
    return get_task(user_id, task_id)


def task_snapshot(user_id: int, task_id: int) -> dict | None:
    """The raw row, for storing as an undo payload."""
    row = get_db().execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def pinned_sessions_for_day(user_id: int, day: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM sessions WHERE user_id = ? AND day = ? AND origin = 'pinned' "
        "AND deleted_at IS NULL ORDER BY start_minute",
        (user_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


def sessions_for_day(user_id: int, day: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM sessions WHERE user_id = ? AND day = ? AND deleted_at IS NULL "
        "ORDER BY start_minute",
        (user_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


def get_session(user_id: int, session_id: int) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM sessions WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (session_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def insert_session(
    user_id: int, task_id: int, day: str, start_minute: int, end_minute: int,
    origin: str = "auto", sequence: int = 1,
) -> int:
    database = get_db()
    cursor = database.execute(
        "INSERT INTO sessions (user_id, task_id, day, start_minute, end_minute, "
        "origin, sequence) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, task_id, day, start_minute, end_minute, origin, sequence),
    )
    database.commit()
    return cursor.lastrowid


def update_session(user_id: int, session_id: int, changes: dict) -> dict | None:
    allowed = {
        k: v for k, v in changes.items()
        if k in {"day", "start_minute", "end_minute", "origin", "status"}
    }
    if not allowed:
        return get_session(user_id, session_id)
    assignments = ", ".join(f"{name} = ?" for name in allowed)
    database = get_db()
    database.execute(
        f"UPDATE sessions SET {assignments}, updated_at = datetime('now') "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (*allowed.values(), session_id, user_id),
    )
    database.commit()
    return get_session(user_id, session_id)


def delete_session(user_id: int, session_id: int) -> bool:
    database = get_db()
    cursor = database.execute(
        "UPDATE sessions SET deleted_at = datetime('now') "
        "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (session_id, user_id),
    )
    database.commit()
    return cursor.rowcount > 0


def clear_auto_sessions(user_id: int, day: str) -> None:
    """Drops the planner's own sessions for a day, leaving pinned ones alone."""
    database = get_db()
    database.execute(
        "DELETE FROM sessions WHERE user_id = ? AND day = ? AND origin = 'auto'",
        (user_id, day),
    )
    database.commit()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def load_settings(user_id: int) -> dict:
    """Stored values merged over the defaults, matching the desktop behaviour."""
    settings = dict(DEFAULT_SETTINGS)
    rows = get_db().execute(
        "SELECT key, value FROM settings WHERE user_id = ?", (user_id,)
    ).fetchall()
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            continue
    return settings


def save_settings(user_id: int, changes: dict) -> dict:
    database = get_db()
    for key, value in changes.items():
        database.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, json.dumps(value)),
        )
    database.commit()
    return load_settings(user_id)


# ---------------------------------------------------------------------------
# Notes and events
# ---------------------------------------------------------------------------
def get_note(user_id: int, day: str) -> str:
    row = get_db().execute(
        "SELECT body FROM notes WHERE user_id = ? AND day = ?", (user_id, day)
    ).fetchone()
    return row["body"] if row else ""


def save_note(user_id: int, day: str, body: str) -> None:
    database = get_db()
    if body.strip():
        database.execute(
            "INSERT INTO notes (user_id, day, body) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET body = excluded.body, "
            "updated_at = datetime('now')",
            (user_id, day, body),
        )
    else:
        database.execute(
            "DELETE FROM notes WHERE user_id = ? AND day = ?", (user_id, day)
        )
    database.commit()


def record_event(
    user_id: int, event_type: str, entity: str | None = None,
    entity_id: int | None = None, details: dict | None = None,
    undo_payload: dict | None = None,
) -> int:
    database = get_db()
    cursor = database.execute(
        "INSERT INTO events (user_id, type, entity, entity_id, details, undo_payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id, event_type, entity, entity_id,
            json.dumps(details) if details is not None else None,
            json.dumps(undo_payload) if undo_payload is not None else None,
        ),
    )
    database.commit()
    return cursor.lastrowid


def latest_undoable_event(user_id: int) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM events WHERE user_id = ? AND undo_payload IS NOT NULL "
        "AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_event_undone(user_id: int, event_id: int) -> None:
    database = get_db()
    database.execute(
        "UPDATE events SET undone_at = datetime('now') WHERE id = ? AND user_id = ?",
        (event_id, user_id),
    )
    database.commit()
