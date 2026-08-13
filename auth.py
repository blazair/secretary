"""
auth.py
=======
Accounts, sessions and the signup gate.

Identity travels in the signed Flask session cookie and nowhere else. There is
no bearer token, so there is nothing forgeable to send.

The first account created needs no invite code and becomes admin, which closes
the open-registration hole the moment the instance is claimed.
"""

from __future__ import annotations

import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import MAX_FAILED_ATTEMPTS, REQUIRE_INVITE, THROTTLE_WINDOW_MINUTES
from db import get_db

blueprint = Blueprint("auth", __name__, url_prefix="/api")

MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 20
MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------------
def current_user_id() -> int | None:
    return session.get("user_id")


def login_required(view):
    """Rejects anonymous callers with JSON rather than a redirect."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = current_user_id()
        if user_id is None:
            return jsonify({"error": "Sign in first."}), 401
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        row = get_db().execute(
            "SELECT is_admin FROM users WHERE id = ?", (current_user_id(),)
        ).fetchone()
        if row is None or not row["is_admin"]:
            return jsonify({"error": "That needs an admin account."}), 403
        return view(*args, **kwargs)

    return wrapped


def client_ip() -> str:
    """The visitor's address.

    Behind the Cloudflare tunnel remote_addr is the tunnel itself, so every
    visitor would share one throttle bucket without this header.
    """
    forwarded = request.headers.get("CF-Connecting-IP")
    if forwarded:
        return forwarded.strip()
    chain = request.headers.get("X-Forwarded-For", "")
    if chain:
        return chain.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------
def _record_attempt(ip: str, username: str | None, ok: bool) -> None:
    database = get_db()
    database.execute(
        "INSERT INTO auth_attempts (ip, username, ok) VALUES (?, ?, ?)",
        (ip, username, 1 if ok else 0),
    )
    database.commit()


def _is_throttled(ip: str) -> bool:
    row = get_db().execute(
        "SELECT COUNT(*) AS failures FROM auth_attempts "
        "WHERE ip = ? AND ok = 0 AND at > datetime('now', ?)",
        (ip, f"-{THROTTLE_WINDOW_MINUTES} minutes"),
    ).fetchone()
    return row["failures"] >= MAX_FAILED_ATTEMPTS


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------
def _account_count() -> int:
    return get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def _claim_invite(code: str) -> str | None:
    """Spends one use of a code. Returns an error sentence, or None on success."""
    database = get_db()
    row = database.execute(
        "SELECT * FROM invite_codes WHERE code = ?", (code.strip(),)
    ).fetchone()
    if row is None:
        return "That invite code is not valid."
    if row["expires_at"] and row["expires_at"] < _now():
        return "That invite code has expired."
    if row["uses"] >= row["max_uses"]:
        return "That invite code has already been used."
    database.execute(
        "UPDATE invite_codes SET uses = uses + 1 WHERE code = ?", (row["code"],)
    )
    return None


def _now() -> str:
    return get_db().execute("SELECT datetime('now') AS t").fetchone()["t"]


def _public_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@blueprint.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or username).strip()
    invite_code = (data.get("invite_code") or "").strip()

    ip = client_ip()
    if _is_throttled(ip):
        return jsonify({"error": "Too many attempts. Try again in 15 minutes."}), 429

    if not username or not password:
        return jsonify({"error": "A username and password are required."}), 400
    if not MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH:
        return jsonify({
            "error": f"Username must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} characters."
        }), 400
    if not username.replace("_", "").replace("-", "").isalnum():
        return jsonify({
            "error": "Username can contain letters, numbers, hyphens and underscores."
        }), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({
            "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        }), 400

    database = get_db()
    is_first_account = _account_count() == 0

    if REQUIRE_INVITE and not is_first_account:
        if not invite_code:
            return jsonify({"error": "An invite code is required to sign up."}), 403
        problem = _claim_invite(invite_code)
        if problem:
            return jsonify({"error": problem}), 403

    if database.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone():
        _record_attempt(ip, username, ok=False)
        return jsonify({"error": "That username is taken."}), 409

    cursor = database.execute(
        "INSERT INTO users (username, password_hash, display_name, is_admin) "
        "VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), display_name, 1 if is_first_account else 0),
    )
    database.commit()

    row = database.execute(
        "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    _record_attempt(ip, username, ok=True)

    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify({"user": _public_user(row)}), 201


@blueprint.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    ip = client_ip()
    if _is_throttled(ip):
        return jsonify({"error": "Too many attempts. Try again in 15 minutes."}), 429

    row = get_db().execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()

    if row is None or not check_password_hash(row["password_hash"], password):
        _record_attempt(ip, username, ok=False)
        return jsonify({"error": "That username and password do not match."}), 401

    _record_attempt(ip, username, ok=True)
    get_db().execute(
        "UPDATE users SET last_seen_at = datetime('now') WHERE id = ?", (row["id"],)
    )
    get_db().commit()

    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify({"user": _public_user(row)})


@blueprint.post("/logout")
def logout():
    session.clear()
    return jsonify({"signed_out": True})


@blueprint.get("/me")
@login_required
def me():
    row = get_db().execute(
        "SELECT * FROM users WHERE id = ?", (current_user_id(),)
    ).fetchone()
    if row is None:
        session.clear()
        return jsonify({"error": "That account no longer exists."}), 401
    return jsonify({"user": _public_user(row)})


@blueprint.patch("/me")
@login_required
def update_me():
    data = request.get_json(silent=True) or {}
    display_name = (data.get("display_name") or "").strip()
    if not display_name:
        return jsonify({"error": "A display name is required."}), 400

    database = get_db()
    database.execute(
        "UPDATE users SET display_name = ? WHERE id = ?",
        (display_name, current_user_id()),
    )
    database.commit()
    row = database.execute(
        "SELECT * FROM users WHERE id = ?", (current_user_id(),)
    ).fetchone()
    return jsonify({"user": _public_user(row)})


@blueprint.get("/invites")
@admin_required
def list_invites():
    rows = get_db().execute(
        "SELECT code, max_uses, uses, expires_at, note, created_at "
        "FROM invite_codes ORDER BY created_at DESC"
    ).fetchall()
    return jsonify({"invites": [dict(row) for row in rows]})


@blueprint.post("/invites")
@admin_required
def create_invite():
    data = request.get_json(silent=True) or {}
    max_uses = int(data.get("max_uses") or 1)
    note = (data.get("note") or "").strip()
    if not 1 <= max_uses <= 100:
        return jsonify({"error": "Uses must be between 1 and 100."}), 400

    code = secrets.token_urlsafe(9)
    database = get_db()
    database.execute(
        "INSERT INTO invite_codes (code, created_by, max_uses, note) VALUES (?, ?, ?, ?)",
        (code, current_user_id(), max_uses, note),
    )
    database.commit()
    return jsonify({"code": code, "max_uses": max_uses, "note": note}), 201
