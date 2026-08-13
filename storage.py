"""
storage.py
==========
Reads and writes the four files in todoapp/data/:

    tasks.json     current state of every task, overwritten on each save
    events.jsonl   append-only history, one JSON object per line
    notes.json     one note per day
    settings.json  working hours, breaks and fixed commitments

Nothing reads events.jsonl yet. It is written from the start so that later
versions have a history to learn from.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import Task, now_as_text

# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------
DATA_DIRECTORY = Path(__file__).parent / "data"
TASKS_FILE = DATA_DIRECTORY / "tasks.json"
EVENTS_FILE = DATA_DIRECTORY / "events.jsonl"
NOTES_FILE = DATA_DIRECTORY / "notes.json"
SETTINGS_FILE = DATA_DIRECTORY / "settings.json"

# ---------------------------------------------------------------------------
# Defaults, used until settings.json says otherwise
# ---------------------------------------------------------------------------
# Kept here as a re-export so existing callers keep working; the values moved
# to defaults.py when the web app arrived.
from defaults import DEFAULT_SETTINGS  # noqa: E402,F401


def ensure_data_directory() -> None:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        # A corrupt file falls back to the default; the next save overwrites it.
        return fallback


def _write_json(path: Path, value) -> None:
    ensure_data_directory()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    stored = _read_json(SETTINGS_FILE, {})
    settings = dict(DEFAULT_SETTINGS)
    settings.update(stored)
    return settings


def save_settings(settings: dict) -> None:
    _write_json(SETTINGS_FILE, settings)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def load_tasks() -> list[Task]:
    stored_list = _read_json(TASKS_FILE, [])
    return [Task.from_dictionary(item) for item in stored_list]


def save_tasks(tasks: list[Task]) -> None:
    _write_json(TASKS_FILE, [task.to_dictionary() for task in tasks])


# ---------------------------------------------------------------------------
# Events - the append-only history
# ---------------------------------------------------------------------------
def record_event(event_type: str, details: dict) -> None:
    """Appends one line to events.jsonl. Never rewrites and never deletes."""
    ensure_data_directory()
    event = {"at": now_as_text(), "type": event_type, **details}
    with EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    events = []
    with EVENTS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip a half-written line
    return events


# ---------------------------------------------------------------------------
# Notes - one per day
# ---------------------------------------------------------------------------
def load_all_notes() -> dict:
    return _read_json(NOTES_FILE, {})


def get_note_for_day(day_iso: str) -> str:
    return load_all_notes().get(day_iso, "")


def save_note_for_day(day_iso: str, text: str) -> None:
    notes = load_all_notes()
    if text.strip():
        notes[day_iso] = text
    else:
        notes.pop(day_iso, None)
    _write_json(NOTES_FILE, notes)
