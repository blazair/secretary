-- schema.sql
-- Every statement is CREATE ... IF NOT EXISTS, so re-running is safe.
-- Applied by init_db.py; later additions go in db.py ensure_schema().

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT
);

-- The signup gate. The first account ever created bypasses this and becomes
-- admin, so there is no seeding step.
CREATE TABLE IF NOT EXISTS invite_codes (
    code       TEXT    PRIMARY KEY,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    max_uses   INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Login throttling. Behind a tunnel the client address arrives in a header,
-- so auth.py resolves it rather than trusting remote_addr.
CREATE TABLE IF NOT EXISTS auth_attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ip       TEXT    NOT NULL,
    username TEXT,
    ok       INTEGER NOT NULL DEFAULT 0,
    at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip ON auth_attempts(ip, at);

-- ---------------------------------------------------------------------------
-- Tasks
-- ---------------------------------------------------------------------------
-- Columns through `note` mirror the Task dataclass in models.py exactly.
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               TEXT    NOT NULL,
    estimated_minutes   INTEGER NOT NULL DEFAULT 30,
    energy_level        TEXT    NOT NULL DEFAULT 'normal'
                                CHECK (energy_level IN ('light', 'normal', 'deep')),
    priority            INTEGER NOT NULL DEFAULT 2
                                CHECK (priority BETWEEN 1 AND 3),
    category            TEXT    NOT NULL DEFAULT 'general',
    due_date            TEXT,
    scheduled_date      TEXT,
    start_time          TEXT,
    created_at          TEXT    NOT NULL,
    is_done             INTEGER NOT NULL DEFAULT 0,
    completed_at        TEXT,
    actual_minutes      INTEGER,
    times_deferred      INTEGER NOT NULL DEFAULT 0,
    note                TEXT    NOT NULL DEFAULT '',
    -- Splitting. A task is only ever split when it cannot fit contiguously.
    is_splittable       INTEGER NOT NULL DEFAULT 1,
    min_session_minutes INTEGER NOT NULL DEFAULT 25,
    max_session_minutes INTEGER,
    -- Soft delete, which is what makes undo possible.
    deleted_at          TEXT,
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_live
    ON tasks(user_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, due_date);

-- ---------------------------------------------------------------------------
-- Sessions: one task occupying N stretches of clock time
-- ---------------------------------------------------------------------------
-- A block the user dragged is a session with origin 'pinned'. The planner
-- reserves those before it places anything else, which is what makes a drag
-- survive replanning.
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    day            TEXT    NOT NULL,
    start_minute   INTEGER NOT NULL,
    end_minute     INTEGER NOT NULL,
    origin         TEXT    NOT NULL DEFAULT 'auto'
                           CHECK (origin IN ('auto', 'pinned')),
    sequence       INTEGER NOT NULL DEFAULT 1,
    status         TEXT    NOT NULL DEFAULT 'planned'
                           CHECK (status IN ('planned', 'active', 'done', 'skipped')),
    logged_seconds INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at     TEXT,
    CHECK (end_minute > start_minute)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_day
    ON sessions(user_id, day) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id);

-- ---------------------------------------------------------------------------
-- Time entries: what turns actual_minutes into a measurement
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS time_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    started_at TEXT    NOT NULL,
    ended_at   TEXT,
    seconds    INTEGER,
    source     TEXT    NOT NULL DEFAULT 'timer'
                       CHECK (source IN ('timer', 'manual', 'auto_closed')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
-- One running timer per user, enforced by the database rather than by code.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_running_timer
    ON time_entries(user_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_time_entries_task ON time_entries(task_id);

-- ---------------------------------------------------------------------------
-- Events: the append-only history, and the undo stack
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    at           TEXT    NOT NULL DEFAULT (datetime('now')),
    type         TEXT    NOT NULL,
    entity       TEXT,
    entity_id    INTEGER,
    details      TEXT,
    undo_payload TEXT,
    undone_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_undo ON events(user_id, id DESC)
    WHERE undo_payload IS NOT NULL AND undone_at IS NULL;

-- ---------------------------------------------------------------------------
-- Notes and settings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day        TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, day)
);

-- Values are JSON-encoded so that fixed_commitments survives as a list.
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key     TEXT    NOT NULL,
    value   TEXT    NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- ---------------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT    NOT NULL UNIQUE,
    p256dh     TEXT    NOT NULL,
    auth       TEXT    NOT NULL,
    user_agent TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    failed_at  TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id   INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL,
    fire_at      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    body         TEXT    NOT NULL DEFAULT '',
    sent_at      TEXT,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(fire_at)
    WHERE sent_at IS NULL AND cancelled_at IS NULL;
