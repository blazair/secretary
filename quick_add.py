"""
quick_add.py
============
Parses one typed line into a Task.

    write essay 90m tomorrow #writing !1 ~deep

Tokens:
    90m 45min 2h 1.5h     estimated duration
    today tomorrow        due date
    mon..sun              due date, the next such weekday
    +3d                   due date, days from now
    2026-08-12            due date, exact
    2pm 2:30pm 14:00      a fixed clock time; the task is pinned there
    at 2pm  @2pm          the same, with the word "at" ignored
    #category             category, which is what calibration groups by
    !1 !2 !3              priority: must, should, maybe
    ~light ~normal ~deep  energy level

A clock time needs either am/pm or a colon, so "2" stays in the title while
"2pm" and "14:00" do not. Giving a time with no date means today.

A token that matches one of these is always removed from the line, and where
two of a kind appear the last one wins. Whatever is left becomes the title,
which keeps stray tokens such as "+3d" out of titles.

The cost is that day words are always read as dates: "call Monday about the
flat" produces the title "call about the flat".
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from models import (
    ALL_ENERGY_LEVELS,
    ENERGY_NORMAL,
    PRIORITY_SHOULD,
    Task,
    create_task,
)

DURATION_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)(m|min|mins|h|hr|hrs)$", re.IGNORECASE)
PRIORITY_PATTERN = re.compile(r"^!([123])$")
CATEGORY_PATTERN = re.compile(r"^#([A-Za-z0-9_-]+)$")
ENERGY_PATTERN = re.compile(r"^~(light|normal|deep)$", re.IGNORECASE)
EXACT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELATIVE_DAY_PATTERN = re.compile(r"^\+(\d+)d$", re.IGNORECASE)

# A clock time must carry am/pm or a colon, otherwise a bare "2" in a title
# would be read as two o'clock.
TIME_WITH_MERIDIEM = re.compile(r"^@?(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.IGNORECASE)
TIME_24_HOUR = re.compile(r"^@?(\d{1,2}):(\d{2})$")
# Swallowed only when the word after it really is a time.
TIME_LEAD_IN_WORDS = ("at", "@")

WEEKDAY_NUMBERS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

DEFAULT_ESTIMATED_MINUTES = 30

QUICK_ADD_HINT = (
    "90m / 2h  ·  today tomorrow fri +3d  ·  at 2pm / 14:00  ·  "
    "#category  ·  !1 !2 !3  ·  ~light ~normal ~deep"
)


def parse_quick_add(text: str, today: date | None = None) -> tuple[Task, list[str]]:
    """Parses one line into a Task.

    Returns the task and a list of short strings describing what was
    recognised, which the interface shows as a confirmation rather than
    guessing silently.
    """
    today = today or date.today()

    estimated_minutes: int | None = None
    priority = PRIORITY_SHOULD
    category = "general"
    energy_level = ENERGY_NORMAL
    due_date_iso: str | None = None
    start_time: str | None = None
    title_words: list[str] = []
    understood: list[str] = []

    words = text.split()
    index = 0
    while index < len(words):
        word = words[index]
        index += 1

        # "at 2pm" - drop the "at" only when a real time follows it.
        if word.lower() in TIME_LEAD_IN_WORDS and index < len(words):
            following_time = _read_time(words[index])
            if following_time is not None:
                start_time = following_time
                index += 1
                continue

        clock_time = _read_time(word)
        if clock_time is not None:
            start_time = clock_time
            continue

        minutes = _read_duration(word)
        if minutes is not None:
            estimated_minutes = minutes
            continue

        match = PRIORITY_PATTERN.match(word)
        if match:
            priority = int(match.group(1))
            continue

        match = CATEGORY_PATTERN.match(word)
        if match:
            category = match.group(1).lower()
            continue

        match = ENERGY_PATTERN.match(word)
        if match:
            energy_level = match.group(1).lower()
            continue

        parsed_date = _read_date(word, today)
        if parsed_date is not None:
            due_date_iso = parsed_date.isoformat()
            continue

        title_words.append(word)

    # Reported in a fixed order rather than typing order, so the confirmation
    # line reads the same way every time.
    # A time with no date means today, since an appointment has to land on a day.
    if start_time is not None and due_date_iso is None:
        due_date_iso = today.isoformat()

    if estimated_minutes is None:
        estimated_minutes = DEFAULT_ESTIMATED_MINUTES
        understood.append(f"{DEFAULT_ESTIMATED_MINUTES}m (assumed)")
    else:
        understood.append(f"{estimated_minutes}m")
    if start_time:
        understood.append(f"at {start_time}")
    if due_date_iso:
        understood.append(f"due {due_date_iso}")
    understood.append(f"#{category}")
    understood.append(f"priority {priority}")
    understood.append(f"~{energy_level}")

    task = create_task(
        title=" ".join(title_words),
        estimated_minutes=estimated_minutes,
        energy_level=energy_level if energy_level in ALL_ENERGY_LEVELS else ENERGY_NORMAL,
        priority=priority,
        category=category,
        due_date=due_date_iso,
        start_time=start_time,
    )
    return task, understood


# ---------------------------------------------------------------------------
# Readers for the individual token types
# ---------------------------------------------------------------------------
# Each returns None when the word is not of that kind.
def _read_duration(word: str) -> int | None:
    match = DURATION_PATTERN.match(word)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    minutes = amount * 60 if unit.startswith("h") else amount
    return max(5, int(round(minutes)))


def _read_time(word: str) -> str | None:
    """'2pm' / '2:30pm' / '14:00' / '@2pm' -> '14:00'. Anything else -> None."""
    match = TIME_WITH_MERIDIEM.match(word)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        if not (1 <= hour <= 12) or minute > 59:
            return None
        if meridiem == "am" and hour == 12:
            hour = 0            # 12am is midnight
        elif meridiem == "pm" and hour != 12:
            hour += 12          # 12pm stays noon
        return f"{hour:02d}:{minute:02d}"

    match = TIME_24_HOUR.match(word)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return f"{hour:02d}:{minute:02d}"

    return None


def _read_date(word: str, today: date) -> date | None:
    lowered = word.lower()

    if lowered in ("today", "tonight"):
        return today
    if lowered == "tomorrow":
        return today + timedelta(days=1)

    if lowered in WEEKDAY_NUMBERS:
        return _next_weekday(today, WEEKDAY_NUMBERS[lowered])

    match = RELATIVE_DAY_PATTERN.match(lowered)
    if match:
        return today + timedelta(days=int(match.group(1)))

    if EXACT_DATE_PATTERN.match(lowered):
        try:
            return date.fromisoformat(lowered)
        except ValueError:
            return None

    return None


def _next_weekday(today: date, target_weekday: int) -> date:
    """The next date with that weekday. If it is that weekday, returns today."""
    days_ahead = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead)
