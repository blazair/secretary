"""
defaults.py
===========
The shape of a working day before any user has changed anything.

Lives apart from both the storage layer and the app so that the pure planning
modules can be handed a settings dict without importing either.
"""

from __future__ import annotations

DEFAULT_SETTINGS = {
    "work_day_starts_at": "09:00",
    "work_day_ends_at": "18:00",
    # Deep tasks are placed inside this window before anything else.
    "deep_work_starts_at": "09:00",
    "deep_work_ends_at": "12:30",
    "break_minutes": 10,
    "minutes_between_breaks": 90,
    # Blocks the planner must schedule around.
    "fixed_commitments": [
        {"label": "Lunch", "start": "13:00", "end": "13:45"},
    ],
    # Used until there is enough history to measure the real figure.
    # See calibration.py.
    "assumed_daily_capacity_minutes": 300,
}

# Settings keys a user is allowed to change through the API. Anything absent
# from this set is rejected rather than silently stored.
EDITABLE_SETTING_KEYS = frozenset(DEFAULT_SETTINGS)

# A session shorter than this is a shard rather than progress, so the splitter
# skips gaps that would only yield one. Overridable per task.
DEFAULT_MIN_SESSION_MINUTES = 25

# A running timer left open overnight would poison the capacity median, so
# entries older than this are closed automatically and excluded from
# calibration.
TIMER_AUTO_CLOSE_HOURS = 8
