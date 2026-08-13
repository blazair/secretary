"""Shared test fixtures. Every test runs against a throwaway database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A Flask app backed by a database created fresh for this test."""
    import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "INSTANCE_DIR", tmp_path)
    monkeypatch.setattr(config, "SECRET_FILE", tmp_path / "secret_key")
    monkeypatch.setattr(config, "REQUIRE_INVITE", True)

    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "INSTANCE_DIR", tmp_path)

    import app as app_module

    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)

    with flask_app.app_context():
        db_module.init_schema()
        db_module.ensure_schema()

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def owner(client):
    """The first account, which needs no invite and becomes admin."""
    response = client.post(
        "/api/register",
        json={"username": "owner", "password": "correct horse battery"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["user"]


def make_invite(client) -> str:
    response = client.post("/api/invites", json={"max_uses": 1})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["code"]


def register(client, username, password="correct horse battery", invite=None):
    payload = {"username": username, "password": password}
    if invite:
        payload["invite_code"] = invite
    return client.post("/api/register", json=payload)


def login(client, username, password="correct horse battery"):
    return client.post("/api/login", json={"username": username, "password": password})
