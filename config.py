"""
config.py
=========
Paths, ports and the session secret. Holds no secret values in source.

The secret resolves in three steps: the SECRETARY_SECRET environment variable,
then a persisted file under instance/, then a freshly generated key which is
written to that file. Generating a new one on every boot would silently log
every user out on restart.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = INSTANCE_DIR / "secretary.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
FRONTEND_DIR = BASE_DIR / "frontend"
SECRET_FILE = INSTANCE_DIR / "secret_key"

PORT = int(os.environ.get("SECRETARY_PORT", "5001"))

# Signup gate. Off only for local development.
REQUIRE_INVITE = os.environ.get("SECRETARY_REQUIRE_INVITE", "1") != "0"

# Set when the app is reachable over HTTPS, which marks the session cookie
# secure. The Cloudflare tunnel terminates TLS, so this is on in that setup.
BEHIND_HTTPS = os.environ.get("SECRETARY_HTTPS", "0") == "1"

SESSION_LIFETIME_DAYS = 30

# Login throttle.
MAX_FAILED_ATTEMPTS = 8
THROTTLE_WINDOW_MINUTES = 15


def load_secret_key() -> str:
    """The session signing key, generated once and kept."""
    from_env = os.environ.get("SECRETARY_SECRET")
    if from_env:
        return from_env

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        stored = SECRET_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_hex(32)
    SECRET_FILE.write_text(generated, encoding="utf-8")
    return generated
