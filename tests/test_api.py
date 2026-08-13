"""The routes: quick-add, edit, undo, sessions, timer, and the plan JSON."""

from __future__ import annotations

from datetime import date

from conftest import login, make_invite, register

TODAY = date.today().isoformat()


def add(client, text):
    response = client.post("/api/tasks", json={"text": text})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


# ---------------------------------------------------------------------------
# Quick add
# ---------------------------------------------------------------------------
def test_quick_add_parses_and_reports_what_it_read(client, owner):
    body = add(client, "write essay 90m today #writing !1 ~deep")

    assert body["task"]["title"] == "write essay"
    assert body["task"]["estimated_minutes"] == 90
    assert body["task"]["energy_level"] == "deep"
    assert "90m" in body["understood"]
    assert "#writing" in body["understood"]
    assert body["message"].startswith('Added "write essay"')


def test_a_fixed_time_lands_on_the_calendar(client, owner):
    add(client, "dentist 30m today at 2pm")
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()

    dentist = next(b for b in day["blocks"] if b["label"] == "dentist")
    assert dentist["start_minute"] == 840
    assert dentist["minutes"] == 30


def test_a_long_task_is_split_across_the_day(client, owner):
    add(client, "write the whole chapter 300m today !1")
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()

    chunks = [b for b in day["blocks"] if b["label"] == "write the whole chapter"]
    assert len(chunks) >= 2
    assert sum(c["minutes"] for c in chunks) == 300
    assert chunks[0]["of"] == len(chunks)


# ---------------------------------------------------------------------------
# The day payload
# ---------------------------------------------------------------------------
def test_day_json_carries_the_waterline_and_a_status_sentence(client, owner):
    add(client, "big piece 400m today !1")
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()

    assert day["capacity_minutes"] == 300
    assert day["waterline_minute"] is not None
    assert day["status"]
    assert day["calibration"]["finished_task_count"] == 0


def test_status_sentence_is_capacity_py_verbatim(app, client, owner):
    """Proof the pure layer survived the port: the API adds nothing to it."""
    from calibration import build_calibration
    from capacity import build_capacity_report, summarise_day
    from scheduler import plan_day
    import repo

    add(client, "write essay 400m today !1")
    add(client, "emails 20m today #admin")
    from_api = client.get(f"/api/plan/day?date={TODAY}").get_json()["status"]

    with app.app_context():
        tasks = repo.load_tasks(owner["id"])
        settings = repo.load_settings(owner["id"])
        calibration = build_calibration(tasks, settings, TODAY)
        plan = plan_day(tasks, TODAY, TODAY, settings, calibration)
        direct = summarise_day(
            build_capacity_report(plan, calibration), plan, tasks, TODAY
        )

    assert from_api == direct
    assert "Overbooked by" in from_api


def test_overflow_is_reported_in_minutes_below_the_day(client, owner):
    add(client, "enormous 900m today !1")
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()

    assert day["overflow_minutes"] > 0
    assert day["overflow_blocks"]
    assert day["overflow_blocks"][0]["is_overflow"] is True
    assert day["overflow_blocks"][0]["start_minute"] >= day["view_end_minute"]


def test_the_view_stretches_for_an_evening_appointment(client, owner):
    add(client, "dinner 90m today at 8pm")
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()
    assert day["view_end_minute"] >= 1290


def test_week_returns_seven_days(client, owner):
    body = client.get("/api/plan/week").get_json()
    assert len(body["days"]) == 7


def test_month_returns_load_without_blocks(client, owner):
    body = client.get(f"/api/plan/month?month={TODAY[:7]}").get_json()
    assert 28 <= len(body["days"]) <= 31
    assert "blocks" not in body["days"][0]


# ---------------------------------------------------------------------------
# Edit, complete, defer, delete, undo
# ---------------------------------------------------------------------------
def test_editing_a_task(client, owner):
    task_id = add(client, "vague thing 30m")["task"]["id"]

    response = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "specific thing", "estimated_minutes": 75},
    )
    assert response.status_code == 200
    assert response.get_json()["task"]["title"] == "specific thing"
    assert response.get_json()["task"]["estimated_minutes"] == 75


def test_completing_a_task_reports_the_difference(client, owner):
    task_id = add(client, "essay 60m today")["task"]["id"]

    body = client.post(
        f"/api/tasks/{task_id}/complete", json={"actual_minutes": 95}
    ).get_json()
    assert body["task"]["actual_minutes"] == 95
    assert body["message"] == "35m longer than estimated."


def test_deferring_three_times_says_so(client, owner):
    task_id = add(client, "call the bank 20m today")["task"]["id"]
    for _ in range(3):
        body = client.post(f"/api/tasks/{task_id}/defer", json={"days": 1}).get_json()
    assert "unmade decision" in body["message"]
    assert body["task"]["times_deferred"] == 3


def test_delete_then_undo_restores_the_task(client, owner):
    task_id = add(client, "temporary 30m")["task"]["id"]

    client.delete(f"/api/tasks/{task_id}", json={})
    assert client.get("/api/tasks").get_json()["tasks"] == []

    body = client.post("/api/undo", json={}).get_json()
    assert body["task"]["title"] == "temporary"
    assert len(client.get("/api/tasks").get_json()["tasks"]) == 1


def test_undo_reverts_an_edit(client, owner):
    task_id = add(client, "original title 30m")["task"]["id"]
    client.patch(f"/api/tasks/{task_id}", json={"title": "changed title"})

    client.post("/api/undo", json={})
    task = client.get(f"/api/tasks/{task_id}").get_json()["task"]
    assert task["title"] == "original title"


def test_undo_with_nothing_to_undo(client, owner):
    response = client.post("/api/undo", json={})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def test_creating_a_session_by_clicking_an_empty_slot(client, owner):
    response = client.post("/api/sessions", json={
        "text": "read the paper", "day": TODAY, "start_minute": 600, "minutes": 45,
    })
    assert response.status_code == 201
    day = response.get_json()["day"]
    block = next(b for b in day["blocks"] if b["label"] == "read the paper")
    assert block["start_minute"] == 600
    assert block["origin"] == "pinned"


def test_dragging_a_session_moves_it_and_it_stays(client, owner):
    created = client.post("/api/sessions", json={
        "text": "focus block", "day": TODAY, "start_minute": 600, "minutes": 60,
    }).get_json()
    session_id = created["session_id"]

    moved = client.patch(f"/api/sessions/{session_id}", json={
        "start_minute": 900, "minutes": 90,
    })
    assert moved.status_code == 200

    day = client.get(f"/api/plan/day?date={TODAY}").get_json()
    block = next(b for b in day["blocks"] if b["label"] == "focus block")
    assert block["start_minute"] == 900
    assert block["minutes"] == 90


def test_nothing_is_scheduled_over_a_pinned_session(client, owner):
    created = client.post("/api/sessions", json={
        "text": "pinned work", "day": TODAY, "start_minute": 600, "minutes": 60,
    }).get_json()
    add(client, "filler 300m today !1")

    day = client.get(f"/api/plan/day?date={TODAY}").get_json()
    for block in day["blocks"]:
        if block["label"] == "pinned work":
            continue
        assert block["end_minute"] <= 600 or block["start_minute"] >= 660


# ---------------------------------------------------------------------------
# Timer
# ---------------------------------------------------------------------------
def test_timer_start_stop_and_the_one_running_rule(client, owner):
    first = add(client, "task one 30m today")["task"]["id"]
    second = add(client, "task two 30m today")["task"]["id"]

    assert client.post("/api/timer/start", json={"task_id": first}).status_code == 200
    assert client.get("/api/timer").get_json()["running"]["task_id"] == first

    clash = client.post("/api/timer/start", json={"task_id": second})
    assert clash.status_code == 409

    assert client.post("/api/timer/stop", json={}).status_code == 200
    assert client.get("/api/timer").get_json()["running"] is None


def test_stopping_with_complete_marks_the_task_done(client, owner):
    task_id = add(client, "measured work 30m today")["task"]["id"]
    client.post("/api/timer/start", json={"task_id": task_id})
    body = client.post("/api/timer/stop", json={"complete": True}).get_json()
    assert body["task"]["is_done"] is True


# ---------------------------------------------------------------------------
# Isolation, across every route that takes an id
# ---------------------------------------------------------------------------
def test_one_user_cannot_touch_another_users_task(client, owner):
    owned = add(client, "private thing 30m today")["task"]["id"]
    session_id = client.post("/api/sessions", json={
        "text": "private session", "day": TODAY, "start_minute": 600, "minutes": 30,
    }).get_json()["session_id"]

    code = make_invite(client)
    client.post("/api/logout", json={})
    register(client, "intruder", invite=code)

    assert client.get(f"/api/tasks/{owned}").status_code == 404
    assert client.patch(f"/api/tasks/{owned}", json={"title": "hijacked"}).status_code == 404
    assert client.post(f"/api/tasks/{owned}/complete", json={}).status_code == 404
    assert client.post(f"/api/tasks/{owned}/defer", json={}).status_code == 404
    assert client.delete(f"/api/tasks/{owned}", json={}).status_code == 404
    assert client.patch(f"/api/sessions/{session_id}", json={"start_minute": 700}).status_code == 404
    assert client.delete(f"/api/sessions/{session_id}", json={}).status_code == 404
    assert client.post("/api/timer/start", json={"task_id": owned}).status_code == 404
    assert client.get("/api/tasks").get_json()["tasks"] == []


def test_signed_out_callers_get_401(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/plan/day").status_code == 401
    assert client.post("/api/tasks", json={"text": "x"}).status_code == 401


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_settings_change_the_plan(client, owner):
    client.patch("/api/settings", json={"work_day_starts_at": "07:00"})
    day = client.get(f"/api/plan/day?date={TODAY}").get_json()
    assert day["day_start_minute"] == 420


def test_unknown_settings_are_refused(client, owner):
    response = client.patch("/api/settings", json={"nonsense": 1})
    assert response.status_code == 400
