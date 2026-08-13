"""
planning.py
===========
Turns a DayPlan into the JSON the browser draws, and works out where the
waterline sits.

The waterline is the one piece of arithmetic that belongs to neither the
scheduler nor capacity.py. Capacity is measured in work minutes; the calendar
axis is clock minutes. Walking the day's task blocks in order and accumulating
until the total crosses capacity converts one into the other, which is why the
line lands after lunch and breaks have been paid for rather than at a fixed
time.
"""

from __future__ import annotations

from calibration import build_calibration
from capacity import build_capacity_report, summarise_day
from models import (
    BLOCK_KIND_TASK,
    DayPlan,
    Task,
    clock_to_minutes,
    friendly_day_name,
    shift_iso_date,
    today_as_iso,
)
from scheduler import plan_day, plan_range
import repo

WEEK_LENGTH = 7


# ---------------------------------------------------------------------------
# Building a plan for one user
# ---------------------------------------------------------------------------
def context_for(user_id: int) -> tuple[list[Task], dict, object, str]:
    tasks = repo.load_tasks(user_id)
    settings = repo.load_settings(user_id)
    today_iso = today_as_iso()
    calibration = build_calibration(tasks, settings, today_iso)
    return tasks, settings, calibration, today_iso


def day_plan_for(user_id: int, day_iso: str) -> tuple[DayPlan, dict, object, list[Task], str]:
    tasks, settings, calibration, today_iso = context_for(user_id)
    plan = plan_day(
        tasks, day_iso, today_iso, settings, calibration,
        pinned_sessions=repo.pinned_sessions_for_day(user_id, day_iso),
    )
    return plan, settings, calibration, tasks, today_iso


# ---------------------------------------------------------------------------
# The waterline
# ---------------------------------------------------------------------------
def find_waterline(plan: DayPlan, capacity_minutes: int) -> int | None:
    """The clock minute at which the day's work reaches capacity.

    None when the day never gets there.
    """
    if capacity_minutes <= 0:
        return None

    accumulated = 0
    task_blocks = sorted(
        (b for b in plan.blocks if b.kind == BLOCK_KIND_TASK),
        key=lambda block: block.start_minute,
    )
    for block in task_blocks:
        if accumulated + block.duration_minutes >= capacity_minutes:
            return block.start_minute + (capacity_minutes - accumulated)
        accumulated += block.duration_minutes
    return None


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def task_json(task: Task) -> dict:
    return {
        "id": int(task.task_id) if task.task_id.isdigit() else task.task_id,
        "title": task.title,
        "estimated_minutes": task.estimated_minutes,
        "energy_level": task.energy_level,
        "priority": task.priority,
        "category": task.category,
        "due_date": task.due_date,
        "scheduled_date": task.scheduled_date,
        "start_time": task.start_time,
        "is_done": task.is_done,
        "completed_at": task.completed_at,
        "actual_minutes": task.actual_minutes,
        "times_deferred": task.times_deferred,
        "note": task.note,
        "is_splittable": task.is_splittable,
        "min_session_minutes": task.min_session_minutes,
        "max_session_minutes": task.max_session_minutes,
    }


def block_json(block, waterline_minute: int | None) -> dict:
    return {
        "session_id": block.session_id,
        "task_id": int(block.task_id) if (block.task_id or "").isdigit() else block.task_id,
        "kind": block.kind,
        "label": block.label,
        "start_minute": block.start_minute,
        "end_minute": block.end_minute,
        "minutes": block.duration_minutes,
        "energy_level": block.energy_level,
        "origin": block.origin,
        "sequence": block.sequence,
        "of": block.of,
        "time_range": block.time_range_text(),
        "submerged": (
            waterline_minute is not None
            and block.kind == BLOCK_KIND_TASK
            and block.start_minute >= waterline_minute
        ),
    }


def day_json(user_id: int, day_iso: str) -> dict:
    plan, settings, calibration, tasks, today_iso = day_plan_for(user_id, day_iso)
    report = build_capacity_report(plan, calibration)
    waterline = find_waterline(plan, calibration.capacity_minutes)

    day_start = clock_to_minutes(settings["work_day_starts_at"])
    day_end = clock_to_minutes(settings["work_day_ends_at"])
    view_start, view_end = day_start, day_end
    for block in plan.blocks:
        view_start = min(view_start, block.start_minute)
        view_end = max(view_end, block.end_minute)
    view_start = (view_start // 60) * 60

    # Minutes that found no room, drawn below the day at the same scale.
    overflow = []
    running = view_end
    for task in plan.unscheduled_tasks:
        placed, owed = plan.partially_placed.get(task.task_id, (0, 0))
        minutes = owed - placed if owed else calibration.realistic_minutes_for(task)
        overflow.append({
            "task_id": int(task.task_id) if task.task_id.isdigit() else task.task_id,
            "label": task.title,
            "minutes": minutes,
            "start_minute": running,
            "end_minute": running + minutes,
            "energy_level": task.energy_level,
            "kind": BLOCK_KIND_TASK,
            "is_overflow": True,
        })
        running += minutes

    return {
        "date": day_iso,
        "friendly": friendly_day_name(day_iso, today_iso),
        "is_today": day_iso == today_iso,
        "day_start_minute": day_start,
        "day_end_minute": day_end,
        "view_start_minute": view_start,
        "view_end_minute": view_end,
        "blocks": [block_json(b, waterline) for b in plan.blocks],
        "overflow_blocks": overflow,
        "waterline_minute": waterline,
        "capacity_minutes": report.capacity_minutes,
        "capacity_is_measured": report.capacity_is_measured,
        "intended_minutes": report.intended_minutes,
        "scheduled_minutes": report.scheduled_minutes,
        "overflow_minutes": report.overflow_minutes,
        "load_ratio": round(report.load_ratio, 3),
        "clashes": plan.clashes,
        "backlog": [task_json(t) for t in plan.backlog],
        "status": summarise_day(report, plan, tasks, today_iso),
        "calibration": {
            "describes": calibration.describe(),
            "overall_multiplier": round(calibration.overall_multiplier, 2),
            "finished_task_count": calibration.finished_task_count,
        },
    }


def week_json(user_id: int, start_iso: str) -> dict:
    tasks, settings, calibration, today_iso = context_for(user_id)
    end_iso = shift_iso_date(start_iso, WEEK_LENGTH - 1)

    pinned_by_day = {
        day: repo.pinned_sessions_for_day(user_id, day)
        for day in _days_between(start_iso, end_iso)
    }
    plans = plan_range(
        tasks, start_iso, end_iso, today_iso, settings, calibration,
        pinned_by_day=pinned_by_day,
    )

    days = []
    for day_iso in _days_between(start_iso, end_iso):
        plan = plans[day_iso]
        report = build_capacity_report(plan, calibration)
        waterline = find_waterline(plan, calibration.capacity_minutes)
        days.append({
            "date": day_iso,
            "friendly": friendly_day_name(day_iso, today_iso),
            "is_today": day_iso == today_iso,
            "blocks": [block_json(b, waterline) for b in plan.blocks],
            "waterline_minute": waterline,
            "intended_minutes": report.intended_minutes,
            "overflow_minutes": report.overflow_minutes,
            "load_ratio": round(report.load_ratio, 3),
        })

    return {
        "start": start_iso,
        "end": end_iso,
        "day_start_minute": clock_to_minutes(settings["work_day_starts_at"]),
        "day_end_minute": clock_to_minutes(settings["work_day_ends_at"]),
        "capacity_minutes": calibration.capacity_minutes,
        "days": days,
        "week_minutes": sum(d["intended_minutes"] for d in days),
    }


def month_json(user_id: int, month: str) -> dict:
    """Load per day, with no blocks. A month of full plans is wasted bytes."""
    tasks, settings, calibration, today_iso = context_for(user_id)
    year, month_number = (int(part) for part in month.split("-"))
    first = f"{year:04d}-{month_number:02d}-01"
    last_day = _days_in_month(year, month_number)
    last = f"{year:04d}-{month_number:02d}-{last_day:02d}"

    plans = plan_range(tasks, first, last, today_iso, settings, calibration)
    days = []
    for day_iso in _days_between(first, last):
        report = build_capacity_report(plans[day_iso], calibration)
        days.append({
            "date": day_iso,
            "is_today": day_iso == today_iso,
            "intended_minutes": report.intended_minutes,
            "overflow_minutes": report.overflow_minutes,
            "load_ratio": round(report.load_ratio, 3),
        })

    return {
        "month": month,
        "capacity_minutes": calibration.capacity_minutes,
        "days": days,
    }


# ---------------------------------------------------------------------------
# Writing the plan down, so timers and reminders have something to attach to
# ---------------------------------------------------------------------------
def materialize_day(user_id: int, day_iso: str) -> dict:
    """Persists the planner's own blocks as sessions, leaving pinned ones alone."""
    plan, _, _, _, _ = day_plan_for(user_id, day_iso)
    repo.clear_auto_sessions(user_id, day_iso)

    for block in plan.blocks:
        if block.kind != BLOCK_KIND_TASK or block.origin == "pinned":
            continue
        if not (block.task_id or "").isdigit():
            continue
        repo.insert_session(
            user_id, int(block.task_id), day_iso,
            block.start_minute, block.end_minute,
            origin="auto", sequence=block.sequence,
        )
    return day_json(user_id, day_iso)


# ---------------------------------------------------------------------------
# Small date helpers
# ---------------------------------------------------------------------------
def _days_between(start_iso: str, end_iso: str) -> list[str]:
    days = []
    current = start_iso
    while current <= end_iso:
        days.append(current)
        current = shift_iso_date(current, 1)
    return days


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following = f"{year + 1:04d}-01-01"
    else:
        following = f"{year:04d}-{month + 1:02d}-01"
    return int(shift_iso_date(following, -1)[-2:])
