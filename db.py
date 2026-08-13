"""
db.py
=====
SQLite connection handling and schema upkeep.

Request-scoped connections hang off flask.g. Background threads call
open_connection() directly, since g belongs to a request.
"""

from __future__ import annotations

import sqlite3

from flask import g

from config import DB_PATH, INSTANCE_DIR, SCHEMA_PATH

# Added after the initial schema shipped. Each runs on every boot and raises
# OperationalError once the change is already present, which is swallowed.
MIGRATIONS: list[str] = []

INDEXES: list[str] = []


def open_connection() -> sqlite3.Connection:
    """A configured connection. Callers outside a request must close it."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    # WAL lets the reminder thread write while requests read.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db() -> sqlite3.Connection:
    """The connection for this request, opened on first use."""
    if "db" not in g:
        g.db = open_connection()
    return g.db


def close_db(exception=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_schema() -> None:
    """Create every table. Safe to run against an existing database."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    connection = open_connection()
    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


def ensure_schema() -> None:
    """Apply post-launch column and index additions."""
    connection = open_connection()
    try:
        for statement in MIGRATIONS + INDEXES:
            try:
                connection.execute(statement)
            except sqlite3.OperationalError:
                pass  # already applied
        connection.commit()
    finally:
        connection.close()


def register(app) -> None:
    app.teardown_appcontext(close_db)
