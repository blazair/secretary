"""
capacity.py
===========
Compares what a day contains against the measured capacity, and turns the
difference into the sentence shown in the status bar.

Every message quotes the numbers behind it and names a specific task where
it suggests dropping one. Where there is no number to quote, nothing is said.
"""

from __future__ import annotations

from dataclasses import dataclass

from models import DayPlan, Task, friendly_day_name, minutes_to_human
from calibration import Calibration, stalled_tasks

# Load thresholds, as a fraction of measured capacity.
COMFORTABLE_LOAD = 0.85
OVER_CAPACITY_LOAD = 1.0
BADLY_OVER_LOAD = 1.25
QUIET_DAY_LOAD = 0.5


@dataclass
class CapacityReport:
    """The arithmetic behind the status messages."""

    day_iso: str
    intended_minutes: int       # everything meant for the day
    scheduled_minutes: int      # everything that found a slot
    overflow_minutes: int       # everything that did not
    capacity_minutes: int       # what actually gets finished on a normal day
    capacity_is_measured: bool
    unscheduled_count: int

    @property
    def load_ratio(self) -> float:
        if self.capacity_minutes <= 0:
            return 0.0
        return self.intended_minutes / self.capacity_minutes

    @property
    def minutes_over_capacity(self) -> int:
        return max(0, self.intended_minutes - self.capacity_minutes)

    @property
    def minutes_of_room_left(self) -> int:
        return max(0, self.capacity_minutes - self.intended_minutes)

    def is_over_capacity(self) -> bool:
        return self.load_ratio > OVER_CAPACITY_LOAD


def build_capacity_report(day_plan: DayPlan, calibration: Calibration) -> CapacityReport:
    return CapacityReport(
        day_iso=day_plan.day_iso,
        intended_minutes=day_plan.total_intended_minutes,
        scheduled_minutes=day_plan.planned_task_minutes,
        overflow_minutes=day_plan.overflow_minutes,
        capacity_minutes=calibration.capacity_minutes,
        capacity_is_measured=calibration.capacity_is_measured,
        unscheduled_count=len(day_plan.unscheduled_tasks),
    )


# ---------------------------------------------------------------------------
# The messages
# ---------------------------------------------------------------------------
def summarise_day(
    report: CapacityReport,
    day_plan: DayPlan,
    all_tasks: list[Task],
    today_iso: str,
) -> str:
    """The standing status line: how loaded the day is, and what is likely to slip."""
    day_name = friendly_day_name(report.day_iso, today_iso).lower()
    load = report.load_ratio
    intended = minutes_to_human(report.intended_minutes)
    capacity = minutes_to_human(report.capacity_minutes)
    basis = "a usual" if report.capacity_is_measured else "an assumed"

    if report.intended_minutes == 0:
        sentences = [f"Nothing booked {day_name}."]
    elif load <= QUIET_DAY_LOAD:
        sentences = [
            f"Light: {intended} planned against {basis} {capacity}. "
            f"Room for {minutes_to_human(report.minutes_of_room_left)} more."
        ]
    elif load <= COMFORTABLE_LOAD:
        sentences = [f"Reasonable: {intended} of {basis} {capacity}."]
    elif load <= OVER_CAPACITY_LOAD:
        sentences = [f"Full: {intended} of {basis} {capacity}. No slack left."]
    elif load <= BADLY_OVER_LOAD:
        sentences = [
            f"Slightly over: {intended} planned against {basis} {capacity}. "
            f"Something will slip."
        ]
    else:
        sentences = [
            f"Overbooked by {minutes_to_human(report.minutes_over_capacity)}: "
            f"{intended} planned against {basis} {capacity}."
        ]

    if load > OVER_CAPACITY_LOAD:
        suggestion = _suggest_something_to_move(day_plan, all_tasks)
        if suggestion:
            sentences.append(suggestion)

    if day_plan.clashes:
        sentences.append("Double booked: " + "; ".join(day_plan.clashes) + ".")

    if report.unscheduled_count:
        sentences.append(
            f"{report.unscheduled_count} task(s) did not fit in the day at all."
        )

    stalled = stalled_tasks(all_tasks)
    if stalled:
        worst = stalled[0]
        sentences.append(
            f'"{worst.title}" has been pushed back {worst.times_deferred} times. '
            f"Do it first or drop it."
        )

    return "  ".join(sentences)


def react_to_new_task(
    new_task: Task,
    report_before: CapacityReport,
    report_after: CapacityReport,
    day_plan_after: DayPlan,
    calibration: Calibration,
    all_tasks: list[Task],
    today_iso: str,
) -> str:
    """The message shown when a task is added.

    Pushes back only when the arithmetic changed for the worse.
    """
    booked = calibration.realistic_minutes_for(new_task)
    lines = [f'Added "{new_task.title}" - booking {minutes_to_human(booked)}.']

    stretched = booked > new_task.estimated_minutes
    if stretched:
        multiplier = calibration.multiplier_for(new_task.category)
        lines.append(
            f"Estimate was {minutes_to_human(new_task.estimated_minutes)}, but "
            f"#{new_task.category} tasks run {multiplier:.2f}x."
        )

    crossed_the_line = (
        not report_before.is_over_capacity() and report_after.is_over_capacity()
    )
    if crossed_the_line:
        lines.append(
            f"That tips today over: {minutes_to_human(report_after.intended_minutes)} "
            f"planned against a usual {minutes_to_human(report_after.capacity_minutes)}."
        )
    elif report_after.is_over_capacity():
        lines.append(
            f"Now {minutes_to_human(report_after.minutes_over_capacity)} over the "
            f"usual day."
        )

    if report_after.is_over_capacity():
        suggestion = _suggest_something_to_move(day_plan_after, all_tasks)
        if suggestion:
            lines.append(suggestion)

    if any(task.task_id == new_task.task_id for task in day_plan_after.unscheduled_tasks):
        lines.append("It did not fit in the day - it is sitting in the overflow list.")

    return " ".join(lines)


def _suggest_something_to_move(day_plan: DayPlan, all_tasks: list[Task]) -> str | None:
    """Names the specific task that is cheapest to move off the day."""
    tasks_by_id = {task.task_id: task for task in all_tasks}

    candidates = [
        tasks_by_id[block.task_id]
        for block in day_plan.blocks
        if block.task_id and block.task_id in tasks_by_id
    ]
    candidates.extend(day_plan.unscheduled_tasks)
    if not candidates:
        return None

    # Lowest priority first, then the furthest deadline, then the biggest,
    # since moving the biggest frees the most of the day. All three want the
    # largest value, which is why this is a single reversed sort.
    droppable = sorted(
        candidates,
        key=lambda task: (task.priority, task.due_date_for_sorting(), task.estimated_minutes),
        reverse=True,
    )[0]
    return (
        f'Cheapest thing to move is "{droppable.title}" '
        f"({minutes_to_human(droppable.estimated_minutes)}, {_priority_word(droppable)})."
    )


def _priority_word(task: Task) -> str:
    return {1: "a must", 2: "a should", 3: "a maybe"}.get(task.priority, "unranked")
