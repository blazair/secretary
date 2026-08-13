"""Splitting, pinned sessions, and the behaviour that must not regress."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from calibration import build_calibration
from defaults import DEFAULT_SETTINGS
from models import Task, create_task, minutes_to_clock
from quick_add import parse_quick_add
from scheduler import build_free_intervals, plan_day, plan_range

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()


def settings() -> dict:
    return dict(DEFAULT_SETTINGS)


def calibration_for(tasks) -> object:
    return build_calibration(tasks, settings(), TODAY_ISO)


def task_from(line: str, **overrides) -> Task:
    task, _ = parse_quick_add(line, TODAY)
    task.task_id = overrides.pop("task_id", task.task_id)
    for name, value in overrides.items():
        setattr(task, name, value)
    return task


def placed_minutes_for(plan, task_id) -> int:
    return sum(
        block.duration_minutes
        for block in plan.blocks
        if block.task_id == task_id and block.kind == "task"
    )


# ---------------------------------------------------------------------------
# The bug this port exists to fix
# ---------------------------------------------------------------------------
def test_default_day_has_no_gap_bigger_than_255_minutes():
    gaps = build_free_intervals(settings())
    assert [(minutes_to_clock(a), minutes_to_clock(b)) for a, b in gaps] == [
        ("09:00", "13:00"), ("13:45", "18:00"),
    ]
    assert max(b - a for a, b in gaps) == 255


@pytest.mark.parametrize("estimate", [240, 255, 256, 300, 420])
def test_long_tasks_now_get_scheduled(estimate):
    """Before splitting, anything over 255 minutes could never be placed."""
    task = task_from(f"long piece {estimate}m today !1")
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    assert placed_minutes_for(plan, task.task_id) == estimate
    assert plan.overflow_minutes == 0
    assert plan.unscheduled_tasks == []


def test_a_task_too_big_for_one_day_reports_the_shortfall():
    """Placing part of it and naming the remainder is the honest answer."""
    task = task_from("enormous 600m today !1")
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    placed = placed_minutes_for(plan, task.task_id)
    assert 0 < placed < 600
    assert plan.overflow_minutes == 600 - placed
    assert plan.partially_placed[task.task_id] == (placed, 600)


def test_an_undated_oversized_task_no_longer_vanishes():
    """It used to reach neither the calendar nor the overflow list."""
    task = task_from("huge undated 600m")
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    assert task in plan.backlog


def test_one_oversized_task_does_not_discard_the_ones_behind_it():
    """The `break` in fill_spare_room used to abort the whole pass."""
    big = task_from("huge undated 600m !3", task_id="big")
    small = task_from("quick note 15m !3", task_id="small")
    tasks = [big, small]

    plan = plan_day(tasks, TODAY_ISO, TODAY_ISO, settings(), calibration_for(tasks))
    assert placed_minutes_for(plan, "small") == 15


def test_a_whole_task_is_never_split():
    task = task_from("one sitting 300m today !1", is_splittable=False)
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    assert placed_minutes_for(plan, task.task_id) == 0
    assert task in plan.unscheduled_tasks


def test_chunks_are_numbered():
    task = task_from("long piece 300m today !1")
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    chunks = [b for b in plan.blocks if b.task_id == task.task_id]
    assert len(chunks) >= 2
    assert [c.sequence for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.of == len(chunks) for c in chunks)


# ---------------------------------------------------------------------------
# Behaviour that must not regress
# ---------------------------------------------------------------------------
def test_blocks_never_overlap():
    lines = [
        "write essay 120m today #writing !1 ~deep",
        "dentist 30m today at 2pm",
        "standup 15m today at 9:30am",
        "emails 20m today #admin",
        "invoices 40m today #admin",
    ]
    tasks = [task_from(line) for line in lines]
    plan = plan_day(tasks, TODAY_ISO, TODAY_ISO, settings(), calibration_for(tasks))

    spans = sorted((b.start_minute, b.end_minute) for b in plan.blocks)
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))


def test_fixed_time_tasks_keep_their_slot_and_skip_the_multiplier():
    history = []
    for _ in range(4):
        finished = create_task("past", 60, category="general")
        finished.is_done = True
        finished.actual_minutes = 120
        finished.completed_at = TODAY_ISO + "T17:00:00"
        history.append(finished)

    timed = task_from("dentist 30m today at 2pm")
    untimed = task_from("errand 30m today")
    tasks = [timed, untimed] + history
    plan = plan_day(tasks, TODAY_ISO, TODAY_ISO, settings(), calibration_for(tasks))

    dentist = next(b for b in plan.blocks if b.label == "dentist")
    assert (dentist.start_minute, dentist.end_minute) == (840, 870)
    assert placed_minutes_for(plan, untimed.task_id) == 60  # 2.0x applied


def test_clashing_fixed_times_are_reported():
    tasks = [
        task_from("dentist 60m today at 2pm"),
        task_from("team call 30m today at 2:30pm"),
    ]
    plan = plan_day(tasks, TODAY_ISO, TODAY_ISO, settings(), calibration_for(tasks))
    assert plan.clashes == ['"team call" overlaps "dentist"']


def test_breaks_are_earned_per_chunk():
    """A task split three ways earns three breaks, not one."""
    task = task_from("long piece 300m today !1", max_session_minutes=90)
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    chunks = [b for b in plan.blocks if b.task_id == task.task_id]
    breaks = [b for b in plan.blocks if b.kind == "break"]
    assert len(chunks) >= 3
    assert len(breaks) >= 2


def test_lunch_can_serve_as_the_break():
    """No break is inserted where a fixed commitment already interrupts work."""
    task = task_from("long piece 300m today !1")
    plan = plan_day([task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]))

    lunch = next(b for b in plan.blocks if b.label == "Lunch")
    just_before_lunch = [b for b in plan.blocks if b.end_minute == lunch.start_minute]
    assert just_before_lunch and just_before_lunch[0].kind == "task"


# ---------------------------------------------------------------------------
# Pinned sessions
# ---------------------------------------------------------------------------
def test_a_pinned_session_survives_replanning():
    task = task_from("dragged thing 60m today")
    pinned = [{
        "id": 7, "task_id": int(task.task_id) if task.task_id.isdigit() else task.task_id,
        "day": TODAY_ISO, "start_minute": 900, "end_minute": 960,
    }]
    pinned[0]["task_id"] = task.task_id

    plan = plan_day(
        [task], TODAY_ISO, TODAY_ISO, settings(), calibration_for([task]),
        pinned_sessions=pinned,
    )

    block = next(b for b in plan.blocks if b.task_id == task.task_id)
    assert (block.start_minute, block.end_minute) == (900, 960)
    assert block.origin == "pinned"
    assert block.session_id == 7


def test_nothing_is_booked_over_a_pinned_session():
    dragged = task_from("dragged 60m today !1", task_id="a")
    other = task_from("other work 300m today !1", task_id="b")
    pinned = [{"id": 1, "task_id": "a", "day": TODAY_ISO,
               "start_minute": 900, "end_minute": 960}]

    plan = plan_day(
        [dragged, other], TODAY_ISO, TODAY_ISO, settings(),
        calibration_for([dragged, other]), pinned_sessions=pinned,
    )

    for block in plan.blocks:
        if block.task_id == "a":
            continue
        assert block.end_minute <= 900 or block.start_minute >= 960


# ---------------------------------------------------------------------------
# Spill across days
# ---------------------------------------------------------------------------
def test_work_spills_onto_the_next_day():
    task = task_from("enormous 600m today !1")
    end = (TODAY + timedelta(days=3)).isoformat()

    plans = plan_range(
        [task], TODAY_ISO, end, TODAY_ISO, settings(), calibration_for([task])
    )
    total = sum(placed_minutes_for(plan, task.task_id) for plan in plans.values())
    assert total == 600


def test_a_task_is_not_replanned_in_full_every_day():
    """Only the remainder carries forward, and it stops once accounted for."""
    task = task_from("enormous 600m today !1")
    end = (TODAY + timedelta(days=3)).isoformat()

    plans = plan_range(
        [task], TODAY_ISO, end, TODAY_ISO, settings(), calibration_for([task])
    )
    per_day = [placed_minutes_for(plans[d], task.task_id) for d in sorted(plans)]

    assert sum(per_day) == 600
    assert per_day[0] > 0 and per_day[1] > 0
    assert per_day[2] == 0 and per_day[3] == 0


def test_the_day_it_is_due_still_reports_the_shortfall():
    """Work moved to tomorrow does not erase today's overbooking."""
    task = task_from("enormous 600m today !1")
    end = (TODAY + timedelta(days=3)).isoformat()

    plans = plan_range(
        [task], TODAY_ISO, end, TODAY_ISO, settings(), calibration_for([task])
    )
    assert plans[TODAY_ISO].overflow_minutes > 0
