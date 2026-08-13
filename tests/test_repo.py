"""The seam: rows in, Task dataclasses out, and one user never seeing another."""

from __future__ import annotations

from datetime import date

from conftest import make_invite, register


def _two_users(client, owner):
    """Returns (owner_id, friend_id) with the client signed in as the friend."""
    code = make_invite(client)
    client.post("/api/logout", json={})
    friend = register(client, "friend", invite=code).get_json()["user"]
    return owner["id"], friend["id"]


def test_tasks_come_back_as_dataclasses(app, client, owner):
    import repo
    from models import Task
    from quick_add import parse_quick_add

    with app.app_context():
        task, _ = parse_quick_add("write essay 90m tomorrow #writing !1 ~deep")
        stored = repo.insert_task(owner["id"], task)

        assert stored.task_id == "1"

        tasks = repo.load_tasks(owner["id"])
        assert len(tasks) == 1
        assert isinstance(tasks[0], Task)
        assert tasks[0].title == "write essay"
        assert tasks[0].estimated_minutes == 90
        assert tasks[0].energy_level == "deep"
        assert tasks[0].priority == 1
        assert tasks[0].category == "writing"
        assert tasks[0].is_splittable is True


def test_one_user_never_sees_another(app, client, owner):
    import repo
    from quick_add import parse_quick_add

    owner_id, friend_id = _two_users(client, owner)

    with app.app_context():
        repo.insert_task(owner_id, parse_quick_add("owner secret 30m")[0])
        repo.insert_task(friend_id, parse_quick_add("friend secret 30m")[0])

        owner_titles = [t.title for t in repo.load_tasks(owner_id)]
        friend_titles = [t.title for t in repo.load_tasks(friend_id)]

        assert owner_titles == ["owner secret"]
        assert friend_titles == ["friend secret"]

        # Reaching across by id returns nothing rather than the other row.
        assert repo.get_task(friend_id, 1) is None
        assert repo.soft_delete_task(friend_id, 1) is False


def test_calibration_runs_on_repo_tasks_unchanged(app, client, owner):
    """calibration.py must accept repo output with no adaptation."""
    import repo
    from calibration import build_calibration
    from models import create_task

    with app.app_context():
        for _ in range(4):
            finished = create_task("past writing", 60, category="writing")
            stored = repo.insert_task(owner["id"], finished)
            repo.set_task_done(owner["id"], int(stored.task_id), actual_minutes=100)

        tasks = repo.load_tasks(owner["id"])
        settings = repo.load_settings(owner["id"])
        calibration = build_calibration(tasks, settings, date.today().isoformat())

        assert round(calibration.multiplier_for("writing"), 2) == 1.67
        assert calibration.finished_task_count == 4


def test_settings_merge_over_defaults_and_keep_lists(app, client, owner):
    import repo
    from defaults import DEFAULT_SETTINGS

    with app.app_context():
        settings = repo.load_settings(owner["id"])
        assert settings == DEFAULT_SETTINGS

        repo.save_settings(owner["id"], {
            "work_day_starts_at": "08:00",
            "fixed_commitments": [
                {"label": "Gym", "start": "07:00", "end": "08:00"},
                {"label": "Lunch", "start": "13:00", "end": "13:45"},
            ],
        })
        settings = repo.load_settings(owner["id"])

        assert settings["work_day_starts_at"] == "08:00"
        assert settings["work_day_ends_at"] == "18:00"   # default survives
        assert len(settings["fixed_commitments"]) == 2   # list survives JSON round trip


def test_soft_delete_then_restore(app, client, owner):
    import repo
    from quick_add import parse_quick_add

    with app.app_context():
        stored = repo.insert_task(owner["id"], parse_quick_add("temporary 30m")[0])
        task_id = int(stored.task_id)

        assert repo.soft_delete_task(owner["id"], task_id) is True
        assert repo.load_tasks(owner["id"]) == []

        restored = repo.restore_task(owner["id"], task_id)
        assert restored is not None
        assert restored.title == "temporary"
        assert len(repo.load_tasks(owner["id"])) == 1
