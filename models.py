"""
models.py
=========
Data shapes shared by the rest of the app.

    Task           - something to be done
    ScheduledBlock - a stretch of a day given to one thing
    DayPlan        - the blocks for a single day

Plus a few date and time helpers. No file access and no interface code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
# How much concentration a task needs. Deep tasks are scheduled first, and
# only inside the deep-work window.
ENERGY_LIGHT = "light"
ENERGY_NORMAL = "normal"
ENERGY_DEEP = "deep"
ALL_ENERGY_LEVELS = (ENERGY_LIGHT, ENERGY_NORMAL, ENERGY_DEEP)

# How much it matters; 1 is highest.
PRIORITY_MUST = 1
PRIORITY_SHOULD = 2
PRIORITY_MAYBE = 3
PRIORITY_LABELS = {
    PRIORITY_MUST: "must",
    PRIORITY_SHOULD: "should",
    PRIORITY_MAYBE: "maybe",
}

# Kinds of block that can appear on the day view.
BLOCK_KIND_TASK = "task"
BLOCK_KIND_BREAK = "break"
BLOCK_KIND_FIXED = "fixed"

# Sort value for a task with no due date.
DATE_FAR_FUTURE = "9999-12-31"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
@dataclass
class Task:
    """A single task.

    Dates are stored as plain "YYYY-MM-DD" text rather than date objects, so
    that saving to JSON is direct and the data file stays readable.
    """

    task_id: str
    title: str
    estimated_minutes: int = 30
    energy_level: str = ENERGY_NORMAL
    priority: int = PRIORITY_SHOULD
    category: str = "general"

    due_date: str | None = None        # the day it must be finished by
    scheduled_date: str | None = None  # the day it is pinned to, if any
    start_time: str | None = None      # "14:00" if it must happen at a set time

    created_at: str = ""
    is_done: bool = False
    completed_at: str | None = None
    actual_minutes: int | None = None  # what it really took; the calibration input
    times_deferred: int = 0
    note: str = ""

    # -- derived values -----------------------------------------------------

    def is_overdue(self, today_iso: str) -> bool:
        return (
            not self.is_done
            and self.due_date is not None
            and self.due_date < today_iso
        )

    def is_due_on_or_before(self, day_iso: str) -> bool:
        return self.due_date is not None and self.due_date <= day_iso

    def due_date_for_sorting(self) -> str:
        return self.due_date or DATE_FAR_FUTURE

    def short_description(self) -> str:
        return f"{self.title} ({self.estimated_minutes}m, {self.energy_level})"

    def has_fixed_time(self) -> bool:
        """True when the task named a clock time and must be placed there."""
        return self.start_time is not None

    # -- JSON conversion ----------------------------------------------------

    def to_dictionary(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "estimated_minutes": self.estimated_minutes,
            "energy_level": self.energy_level,
            "priority": self.priority,
            "category": self.category,
            "due_date": self.due_date,
            "scheduled_date": self.scheduled_date,
            "start_time": self.start_time,
            "created_at": self.created_at,
            "is_done": self.is_done,
            "completed_at": self.completed_at,
            "actual_minutes": self.actual_minutes,
            "times_deferred": self.times_deferred,
            "note": self.note,
        }

    @staticmethod
    def from_dictionary(stored: dict) -> "Task":
        """Rebuilds a Task from JSON, tolerating fields added in later versions."""
        return Task(
            task_id=stored.get("task_id", new_task_id()),
            title=stored.get("title", "(untitled)"),
            estimated_minutes=int(stored.get("estimated_minutes", 30)),
            energy_level=stored.get("energy_level", ENERGY_NORMAL),
            priority=int(stored.get("priority", PRIORITY_SHOULD)),
            category=stored.get("category", "general"),
            due_date=stored.get("due_date"),
            scheduled_date=stored.get("scheduled_date"),
            start_time=stored.get("start_time"),
            created_at=stored.get("created_at", ""),
            is_done=bool(stored.get("is_done", False)),
            completed_at=stored.get("completed_at"),
            actual_minutes=stored.get("actual_minutes"),
            times_deferred=int(stored.get("times_deferred", 0)),
            note=stored.get("note", ""),
        )


def new_task_id() -> str:
    """Short unique id, kept short because it appears in the data files."""
    return uuid.uuid4().hex[:8]


def create_task(
    title: str,
    estimated_minutes: int = 30,
    energy_level: str = ENERGY_NORMAL,
    priority: int = PRIORITY_SHOULD,
    category: str = "general",
    due_date: str | None = None,
    scheduled_date: str | None = None,
    start_time: str | None = None,
) -> Task:
    """The single construction point, so defaults and created_at are always set."""
    return Task(
        task_id=new_task_id(),
        title=title.strip() or "(untitled)",
        estimated_minutes=max(5, int(estimated_minutes)),
        energy_level=energy_level if energy_level in ALL_ENERGY_LEVELS else ENERGY_NORMAL,
        priority=priority if priority in PRIORITY_LABELS else PRIORITY_SHOULD,
        category=category or "general",
        due_date=due_date,
        scheduled_date=scheduled_date,
        start_time=start_time,
        created_at=now_as_text(),
    )


# ---------------------------------------------------------------------------
# ScheduledBlock
# ---------------------------------------------------------------------------
@dataclass
class ScheduledBlock:
    """A stretch of one day, measured in minutes since midnight."""

    start_minute: int
    end_minute: int
    label: str
    kind: str = BLOCK_KIND_TASK
    task_id: str | None = None
    energy_level: str = ENERGY_NORMAL

    @property
    def duration_minutes(self) -> int:
        return self.end_minute - self.start_minute

    def time_range_text(self) -> str:
        return f"{minutes_to_clock(self.start_minute)}-{minutes_to_clock(self.end_minute)}"


@dataclass
class DayPlan:
    """The result of planning one day."""

    day_iso: str
    blocks: list[ScheduledBlock] = field(default_factory=list)
    unscheduled_tasks: list[Task] = field(default_factory=list)
    planned_task_minutes: int = 0      # minutes that found a slot
    overflow_minutes: int = 0          # minutes that did not fit
    clashes: list[str] = field(default_factory=list)  # double-booked fixed times

    @property
    def total_intended_minutes(self) -> int:
        return self.planned_task_minutes + self.overflow_minutes


# ---------------------------------------------------------------------------
# Time and date helpers
# ---------------------------------------------------------------------------
def clock_to_minutes(clock_text: str) -> int:
    """'09:30' -> 570 minutes past midnight."""
    hours_text, _, minutes_text = clock_text.partition(":")
    return int(hours_text) * 60 + int(minutes_text or 0)


def minutes_to_clock(total_minutes: int) -> str:
    """570 -> '09:30'."""
    total_minutes = max(0, int(total_minutes))
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def minutes_to_human(total_minutes: int) -> str:
    """95 -> '1h 35m'. Used in the status messages."""
    total_minutes = int(round(total_minutes))
    hours, minutes = divmod(max(0, total_minutes), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def today_as_iso() -> str:
    return date.today().isoformat()


def now_as_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def shift_iso_date(day_iso: str, days: int) -> str:
    return (date.fromisoformat(day_iso) + timedelta(days=days)).isoformat()


def friendly_day_name(day_iso: str, today_iso: str) -> str:
    """'Today', 'Tomorrow', 'Yesterday', or 'Tue 12 Aug'."""
    if day_iso == today_iso:
        return "Today"
    if day_iso == shift_iso_date(today_iso, 1):
        return "Tomorrow"
    if day_iso == shift_iso_date(today_iso, -1):
        return "Yesterday"
    return date.fromisoformat(day_iso).strftime("%a %d %b")
