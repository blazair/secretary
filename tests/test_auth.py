"""Accounts, the invite gate, and the throttle."""

from __future__ import annotations

from conftest import login, make_invite, register


def test_first_account_needs_no_invite_and_becomes_admin(client):
    response = register(client, "owner")
    assert response.status_code == 201
    assert response.get_json()["user"]["is_admin"] is True


def test_second_account_is_refused_without_an_invite(client, owner):
    response = register(client, "friend")
    assert response.status_code == 403
    assert "invite code" in response.get_json()["error"]


def test_second_account_succeeds_with_an_invite(client, owner):
    code = make_invite(client)
    client.post("/api/logout", json={})

    response = register(client, "friend", invite=code)
    assert response.status_code == 201
    assert response.get_json()["user"]["is_admin"] is False


def test_an_invite_cannot_be_spent_twice(client, owner):
    code = make_invite(client)
    client.post("/api/logout", json={})

    assert register(client, "friend", invite=code).status_code == 201
    client.post("/api/logout", json={})

    response = register(client, "another", invite=code)
    assert response.status_code == 403
    assert "already been used" in response.get_json()["error"]


def test_duplicate_username_is_refused(client, owner):
    code = make_invite(client)
    client.post("/api/logout", json={})
    response = register(client, "owner", invite=code)
    assert response.status_code == 409


def test_short_password_is_refused(client):
    response = register(client, "owner", password="short")
    assert response.status_code == 400
    assert "at least 8" in response.get_json()["error"]


def test_login_and_me_round_trip(client, owner):
    client.post("/api/logout", json={})
    assert client.get("/api/me").status_code == 401

    assert login(client, "owner").status_code == 200
    body = client.get("/api/me").get_json()
    assert body["user"]["username"] == "owner"


def test_wrong_password_is_refused(client, owner):
    client.post("/api/logout", json={})
    response = login(client, "owner", password="wrong password here")
    assert response.status_code == 401


def test_repeated_failures_are_throttled(client, owner):
    client.post("/api/logout", json={})
    for _ in range(8):
        login(client, "owner", password="wrong password here")

    response = login(client, "owner")
    assert response.status_code == 429
    assert "Too many attempts" in response.get_json()["error"]


def test_invites_need_an_admin(client, owner):
    code = make_invite(client)
    client.post("/api/logout", json={})
    register(client, "friend", invite=code)

    response = client.post("/api/invites", json={"max_uses": 1})
    assert response.status_code == 403


def test_mutations_must_be_json(client, owner):
    response = client.post("/api/logout", data="not json")
    assert response.status_code == 415


def test_session_cookie_flags(client, owner):
    cookie = None
    for header in client.post(
        "/api/login", json={"username": "owner", "password": "correct horse battery"}
    ).headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            cookie = header
    assert cookie is not None
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_api_404_stays_json(client):
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.get_json()["error"]
