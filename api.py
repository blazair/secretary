"""
api.py
======
Every route below /api that needs an account.

Routes read the request, call repo and planning, and return JSON. The uniform
contract is that success returns the resource and failure returns
{"error": "<sentence>"} with a status code, so the browser can print the
sentence without a translation layer.

An id belonging to another account returns 404 rather than 403, since 403
would confirm the row exists.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import planning
import repo
from auth import current_user_id, login_required
from calibration import build_calibration
from capacity import build_capacity_report, react_to_new_task
from defaults import EDITABLE_SETTING_KEYS, TIMER_AUTO_CLOSE_HOURS
from db import get_db
from models import shift_iso_date, today_as_iso
from quick_add import parse_quick_add
from scheduler import plan_day

blueprint = Blueprint("api", __name__, url_prefix="/api")

MISSING = "No such task."


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _day_argument(name: str = "date") -> str:
    return request.args.get(name) or today_as_iso()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@blueprint.get("/tasks")
@login_required
def list_tasks():
    include_done = request.args.get("include_done") == "1"
    tasks = repo.load_tasks(current_user_id(), include_done=include_done)
    return jsonify({"tasks": [planning.task_json(task) for task in tasks]})


@blueprint.post("/tasks")
@login_required
def create_task_route():
    """The quick-add path. One typed line in, a task and a verdict out."""
    user_id = current_user_id()
    text = (_body().get("text") or "").strip()
    if not text:
        return jsonify({"error": "Type something to add."}), 400

    tasks_before, settings, calibration, today_iso = planning.context_for(user_id)
    plan_before = plan_day(
        tasks_before, today_iso, today_iso, settings, calibration,
        pinned_sessions=repo.pinned_sessions_for_day(user_id, today_iso),
    )
    report_before = build_capacity_report(plan_before, calibration)

    task, understood = parse_quick_add(text)
    stored = repo.insert_task(user_id, task)
    repo.record_event(
        user_id, "task_created", "task", int(stored.task_id),
        details={"title": stored.title, "raw_input": text,
                 "estimated_minutes": stored.estimated_minutes,
                 "category": stored.category},
    )

    tasks_after, settings, calibration, _ = planning.context_for(user_id)
    plan_after = plan_day(
        tasks_after, today_iso, today_iso, settings, calibration,
        pinned_sessions=repo.pinned_sessions_for_day(user_id, today_iso),
    )
    report_after = build_capacity_report(plan_after, calibration)

    message = react_to_new_task(
        stored, report_before, report_after, plan_after,
        calibration, tasks_after, today_iso,
    )
    return jsonify({
        "task": planning.task_json(stored),
        "understood": understood,
        "message": message,
    }), 201


@blueprint.get("/tasks/<int:task_id>")
@login_required
def get_task_route(task_id: int):
    task = repo.get_task(current_user_id(), task_id)
    if task is None:
        return jsonify({"error": MISSING}), 404
    return jsonify({"task": planning.task_json(task)})


@blueprint.patch("/tasks/<int:task_id>")
@login_required
def edit_task(task_id: int):
    """Editing a task, which the desktop build could never do."""
    user_id = current_user_id()
    before = repo.task_snapshot(user_id, task_id)
    if before is None or before["deleted_at"]:
        return jsonify({"error": MISSING}), 404

    changes = {k: v for k, v in _body().items() if k in repo.EDITABLE_TASK_FIELDS}
    if not changes:
        return jsonify({"error": "Nothing in that request can be changed."}), 400

    if "estimated_minutes" in changes:
        try:
            changes["estimated_minutes"] = max(5, int(changes["estimated_minutes"]))
        except (TypeError, ValueError):
            return jsonify({"error": "Estimate must be a number of minutes."}), 400

    updated = repo.update_task(user_id, task_id, changes)
    repo.record_event(
        user_id, "task_edited", "task", task_id,
        details={"changed": sorted(changes)}, undo_payload=before,
    )
    return jsonify({"task": planning.task_json(updated)})


@blueprint.post("/tasks/<int:task_id>/complete")
@login_required
def complete_task(task_id: int):
    user_id = current_user_id()
    before = repo.task_snapshot(user_id, task_id)
    if before is None or before["deleted_at"]:
        return jsonify({"error": MISSING}), 404

    given = _body().get("actual_minutes")
    if given is None:
        given = _logged_minutes(user_id, task_id) or before["estimated_minutes"]
    try:
        actual_minutes = max(1, int(given))
    except (TypeError, ValueError):
        return jsonify({"error": "Actual minutes must be a number."}), 400

    task = repo.set_task_done(user_id, task_id, actual_minutes)
    repo.record_event(
        user_id, "task_completed", "task", task_id,
        details={"estimated_minutes": before["estimated_minutes"],
                 "actual_minutes": actual_minutes,
                 "category": before["category"]},
        undo_payload=before,
    )

    difference = actual_minutes - before["estimated_minutes"]
    if difference > 0:
        verdict = f"{difference}m longer than estimated."
    elif difference < 0:
        verdict = f"{-difference}m faster than estimated."
    else:
        verdict = "Exactly as estimated."

    return jsonify({"task": planning.task_json(task), "message": verdict})


@blueprint.post("/tasks/<int:task_id>/reopen")
@login_required
def reopen_task_route(task_id: int):
    user_id = current_user_id()
    before = repo.task_snapshot(user_id, task_id)
    if before is None:
        return jsonify({"error": MISSING}), 404
    task = repo.reopen_task(user_id, task_id)
    repo.record_event(user_id, "task_reopened", "task", task_id, undo_payload=before)
    return jsonify({"task": planning.task_json(task)})


@blueprint.post("/tasks/<int:task_id>/defer")
@login_required
def defer_task_route(task_id: int):
    user_id = current_user_id()
    before = repo.task_snapshot(user_id, task_id)
    if before is None or before["deleted_at"]:
        return jsonify({"error": MISSING}), 404

    days = int(_body().get("days") or 1)
    today_iso = today_as_iso()
    start_from = max(before["scheduled_date"] or today_iso, today_iso)
    new_day = shift_iso_date(start_from, days)

    task = repo.defer_task(user_id, task_id, new_day)
    repo.record_event(
        user_id, "task_deferred", "task", task_id,
        details={"moved_to": new_day, "times_deferred": task.times_deferred},
        undo_payload=before,
    )

    message = f'"{task.title}" moved to {new_day}.'
    if task.times_deferred >= 3:
        message += (
            f" That is {task.times_deferred} times now. "
            "A repeatedly moved task usually signals an unmade decision."
        )
    return jsonify({"task": planning.task_json(task), "message": message})


@blueprint.delete("/tasks/<int:task_id>")
@login_required
def delete_task_route(task_id: int):
    user_id = current_user_id()
    before = repo.task_snapshot(user_id, task_id)
    if before is None or before["deleted_at"]:
        return jsonify({"error": MISSING}), 404

    repo.soft_delete_task(user_id, task_id)
    event_id = repo.record_event(
        user_id, "task_deleted", "task", task_id,
        details={"title": before["title"], "times_deferred": before["times_deferred"]},
        undo_payload=before,
    )
    return jsonify({
        "deleted": task_id,
        "undo_event_id": event_id,
        "message": f'Deleted "{before["title"]}".',
    })


@blueprint.post("/undo")
@login_required
def undo():
    user_id = current_user_id()
    event = repo.latest_undoable_event(user_id)
    if event is None:
        return jsonify({"error": "Nothing to undo."}), 400

    import json

    payload = json.loads(event["undo_payload"])
    task_id = event["entity_id"]

    if event["type"] == "task_deleted":
        repo.restore_task(user_id, task_id)
        message = f'Restored "{payload["title"]}".'
    else:
        restorable = {
            key: payload[key]
            for key in repo.EDITABLE_TASK_FIELDS
            if key in payload
        }
        repo.update_task(user_id, task_id, restorable)
        if event["type"] in ("task_completed", "task_reopened"):
            if payload["is_done"]:
                repo.set_task_done(user_id, task_id, payload["actual_minutes"])
            else:
                repo.reopen_task(user_id, task_id)
        message = f'Reverted "{payload["title"]}".'

    repo.mark_event_undone(user_id, event["id"])
    task = repo.get_task(user_id, task_id)
    return jsonify({
        "task": planning.task_json(task) if task else None,
        "message": message,
    })


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
@blueprint.get("/plan/day")
@login_required
def plan_day_route():
    return jsonify(planning.day_json(current_user_id(), _day_argument()))


@blueprint.get("/plan/week")
@login_required
def plan_week_route():
    start = request.args.get("start") or _monday_of(today_as_iso())
    return jsonify(planning.week_json(current_user_id(), start))


@blueprint.get("/plan/month")
@login_required
def plan_month_route():
    month = request.args.get("month") or today_as_iso()[:7]
    return jsonify(planning.month_json(current_user_id(), month))


@blueprint.post("/plan/day/materialize")
@login_required
def materialize_route():
    day = _body().get("date") or today_as_iso()
    return jsonify(planning.materialize_day(current_user_id(), day))


def _monday_of(day_iso: str) -> str:
    from datetime import date

    parsed = date.fromisoformat(day_iso)
    return shift_iso_date(day_iso, -parsed.weekday())


# ---------------------------------------------------------------------------
# Sessions: the drag, resize and click-empty-slot endpoints
# ---------------------------------------------------------------------------
@blueprint.post("/sessions")
@login_required
def create_session():
    """Places a block at a chosen time, creating the task too when given text."""
    user_id = current_user_id()
    data = _body()
    day = data.get("day") or today_as_iso()
    start_minute = data.get("start_minute")
    minutes = int(data.get("minutes") or 30)

    if start_minute is None:
        return jsonify({"error": "A start time is required."}), 400
    start_minute = int(start_minute)

    task_id = data.get("task_id")
    if task_id is None:
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Type something to add."}), 400
        task, _ = parse_quick_add(text)
        task.estimated_minutes = minutes
        stored = repo.insert_task(user_id, task)
        task_id = int(stored.task_id)
        repo.record_event(
            user_id, "task_created", "task", task_id,
            details={"title": stored.title, "raw_input": text},
        )
    else:
        task_id = int(task_id)
        if repo.get_task(user_id, task_id) is None:
            return jsonify({"error": MISSING}), 404

    session_id = repo.insert_session(
        user_id, task_id, day, start_minute, start_minute + minutes,
        origin="pinned",
    )
    repo.record_event(
        user_id, "session_pinned", "session", session_id,
        details={"day": day, "start_minute": start_minute, "minutes": minutes},
    )
    return jsonify({"session_id": session_id, "day": planning.day_json(user_id, day)}), 201


@blueprint.patch("/sessions/<int:session_id>")
@login_required
def move_session(session_id: int):
    """The drag and resize commit. Returns the whole replanned day."""
    user_id = current_user_id()
    session = repo.get_session(user_id, session_id)
    if session is None:
        return jsonify({"error": "No such session."}), 404

    data = _body()
    day = data.get("day") or session["day"]
    start_minute = int(data.get("start_minute", session["start_minute"]))
    minutes = int(data.get("minutes") or (session["end_minute"] - session["start_minute"]))
    if minutes < 5:
        return jsonify({"error": "A session must be at least 5 minutes."}), 400

    repo.update_session(user_id, session_id, {
        "day": day,
        "start_minute": start_minute,
        "end_minute": start_minute + minutes,
        "origin": "pinned",
    })
    repo.record_event(
        user_id, "session_moved", "session", session_id,
        details={"day": day, "start_minute": start_minute, "minutes": minutes},
    )
    return jsonify({"day": planning.day_json(user_id, day)})


@blueprint.delete("/sessions/<int:session_id>")
@login_required
def unpin_session(session_id: int):
    user_id = current_user_id()
    session = repo.get_session(user_id, session_id)
    if session is None:
        return jsonify({"error": "No such session."}), 404
    repo.delete_session(user_id, session_id)
    return jsonify({"day": planning.day_json(user_id, session["day"])})


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------
def _logged_minutes(user_id: int, task_id: int) -> int:
    row = get_db().execute(
        "SELECT COALESCE(SUM(seconds), 0) AS total FROM time_entries "
        "WHERE user_id = ? AND task_id = ? AND source != 'auto_closed'",
        (user_id, task_id),
    ).fetchone()
    return round(row["total"] / 60)


def _close_stale_timers(user_id: int) -> None:
    """A timer left running overnight would poison the capacity median."""
    database = get_db()
    database.execute(
        "UPDATE time_entries SET ended_at = datetime('now'), "
        "seconds = CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER), "
        "source = 'auto_closed' "
        "WHERE user_id = ? AND ended_at IS NULL AND started_at < datetime('now', ?)",
        (user_id, f"-{TIMER_AUTO_CLOSE_HOURS} hours"),
    )
    database.commit()


@blueprint.get("/timer")
@login_required
def timer_state():
    user_id = current_user_id()
    _close_stale_timers(user_id)
    row = get_db().execute(
        "SELECT te.*, t.title FROM time_entries te JOIN tasks t ON t.id = te.task_id "
        "WHERE te.user_id = ? AND te.ended_at IS NULL",
        (user_id,),
    ).fetchone()
    if row is None:
        return jsonify({"running": None})
    elapsed = get_db().execute(
        "SELECT CAST((julianday('now') - julianday(?)) * 86400 AS INTEGER) AS s",
        (row["started_at"],),
    ).fetchone()["s"]
    return jsonify({"running": {
        "task_id": row["task_id"], "title": row["title"],
        "started_at": row["started_at"], "elapsed_seconds": max(0, elapsed),
    }})


@blueprint.post("/timer/start")
@login_required
def timer_start():
    user_id = current_user_id()
    _close_stale_timers(user_id)
    data = _body()
    task_id = int(data.get("task_id") or 0)
    if repo.get_task(user_id, task_id) is None:
        return jsonify({"error": MISSING}), 404

    database = get_db()
    running = database.execute(
        "SELECT id FROM time_entries WHERE user_id = ? AND ended_at IS NULL",
        (user_id,),
    ).fetchone()
    if running:
        return jsonify({"error": "Another timer is already running."}), 409

    database.execute(
        "INSERT INTO time_entries (user_id, task_id, session_id, started_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (user_id, task_id, data.get("session_id")),
    )
    database.commit()
    repo.record_event(user_id, "timer_started", "task", task_id)
    return jsonify({"started": True})


@blueprint.post("/timer/stop")
@login_required
def timer_stop():
    user_id = current_user_id()
    database = get_db()
    row = database.execute(
        "SELECT * FROM time_entries WHERE user_id = ? AND ended_at IS NULL",
        (user_id,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "No timer is running."}), 400

    database.execute(
        "UPDATE time_entries SET ended_at = datetime('now'), "
        "seconds = CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER) "
        "WHERE id = ?",
        (row["id"],),
    )
    database.commit()
    repo.record_event(user_id, "timer_stopped", "task", row["task_id"])

    total = _logged_minutes(user_id, row["task_id"])
    if _body().get("complete"):
        task = repo.set_task_done(user_id, row["task_id"], max(1, total))
        return jsonify({
            "task": planning.task_json(task),
            "logged_minutes": total,
            "message": f"Logged {total}m and marked it done.",
        })
    return jsonify({"logged_minutes": total, "message": f"Logged {total}m."})


# ---------------------------------------------------------------------------
# Notes and settings
# ---------------------------------------------------------------------------
@blueprint.get("/notes/<day>")
@login_required
def get_note_route(day: str):
    return jsonify({"day": day, "body": repo.get_note(current_user_id(), day)})


@blueprint.put("/notes/<day>")
@login_required
def put_note_route(day: str):
    repo.save_note(current_user_id(), day, _body().get("body") or "")
    return jsonify({"day": day, "saved": True})


@blueprint.get("/settings")
@login_required
def get_settings_route():
    return jsonify({"settings": repo.load_settings(current_user_id())})


@blueprint.patch("/settings")
@login_required
def patch_settings_route():
    changes = {k: v for k, v in _body().items() if k in EDITABLE_SETTING_KEYS}
    if not changes:
        return jsonify({"error": "No recognised settings in that request."}), 400
    return jsonify({"settings": repo.save_settings(current_user_id(), changes)})
