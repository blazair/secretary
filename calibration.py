"""
calibration.py
==============
Measures two numbers from finished tasks.

    estimation multiplier   median of actual / estimated minutes, per
                            category. A multiplier of 1.75 for #writing means
                            the planner books 105 minutes for a 60 minute
                            estimate.

    daily capacity          median minutes finished on days that had any work.
                            Not the number of minutes in the working day.

Medians rather than means, so that one unusually long task does not skew the
result. Both fall back to a default until there are enough samples, and the
multiplier is clamped to a sensible range.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from models import Task, shift_iso_date

# Below this many finished tasks, a category multiplier is noise, so the
# overall multiplier is used instead.
MINIMUM_SAMPLES_PER_CATEGORY = 3
MINIMUM_SAMPLES_OVERALL = 3
# Working days needed before capacity is measured rather than assumed.
MINIMUM_DAYS_FOR_CAPACITY = 3
DAYS_OF_HISTORY_TO_USE = 21

# Bounds on the multiplier, so a few unusual days cannot distort the plan.
SMALLEST_SENSIBLE_MULTIPLIER = 0.5
LARGEST_SENSIBLE_MULTIPLIER = 3.0

# Deferral count at which a task gets flagged.
DEFERRALS_BEFORE_CONCERN = 3


class Calibration:
    """The measured values, built once per refresh and then read only."""

    def __init__(
        self,
        multiplier_by_category: dict[str, float],
        overall_multiplier: float,
        capacity_minutes: int,
        capacity_is_measured: bool,
        finished_task_count: int,
    ) -> None:
        self.multiplier_by_category = multiplier_by_category
        self.overall_multiplier = overall_multiplier
        self.capacity_minutes = capacity_minutes
        self.capacity_is_measured = capacity_is_measured
        self.finished_task_count = finished_task_count

    def multiplier_for(self, category: str) -> float:
        return self.multiplier_by_category.get(category, self.overall_multiplier)

    def realistic_minutes_for(self, task: Task) -> int:
        """The minutes the planner should book for this task."""
        return max(5, int(round(task.estimated_minutes * self.multiplier_for(task.category))))

    def describe(self) -> str:
        """One line for the status bar, stating what these numbers rest on."""
        if self.finished_task_count < MINIMUM_SAMPLES_OVERALL:
            return (
                f"Still learning - {self.finished_task_count} finished tasks logged. "
                f"Using an assumed capacity of {self.capacity_minutes}m/day."
            )
        source = "measured" if self.capacity_is_measured else "assumed"
        return (
            f"From {self.finished_task_count} finished tasks: estimates run "
            f"{self.overall_multiplier:.2f}x, and about {self.capacity_minutes}m "
            f"gets finished on a working day ({source})."
        )

    def worst_estimated_categories(self, limit: int = 2) -> list[tuple[str, float]]:
        """The categories with the largest estimation error, worst first."""
        ranked = sorted(
            self.multiplier_by_category.items(),
            key=lambda pair: abs(pair[1] - 1.0),
            reverse=True,
        )
        return [pair for pair in ranked if abs(pair[1] - 1.0) >= 0.2][:limit]


def build_calibration(tasks: list[Task], settings: dict, today_iso: str) -> Calibration:
    """Measures both numbers from the task list, from scratch."""
    finished = [
        task
        for task in tasks
        if task.is_done and task.actual_minutes and task.estimated_minutes
    ]

    ratios_by_category: dict[str, list[float]] = defaultdict(list)
    all_ratios: list[float] = []
    for task in finished:
        ratio = task.actual_minutes / task.estimated_minutes
        ratios_by_category[task.category].append(ratio)
        all_ratios.append(ratio)

    overall_multiplier = 1.0
    if len(all_ratios) >= MINIMUM_SAMPLES_OVERALL:
        overall_multiplier = _clamp_multiplier(median(all_ratios))

    multiplier_by_category = {
        category: _clamp_multiplier(median(ratios))
        for category, ratios in ratios_by_category.items()
        if len(ratios) >= MINIMUM_SAMPLES_PER_CATEGORY
    }

    capacity_minutes, capacity_is_measured = _measure_daily_capacity(
        finished, settings, today_iso
    )

    return Calibration(
        multiplier_by_category=multiplier_by_category,
        overall_multiplier=overall_multiplier,
        capacity_minutes=capacity_minutes,
        capacity_is_measured=capacity_is_measured,
        finished_task_count=len(finished),
    )


def _measure_daily_capacity(
    finished_tasks: list[Task], settings: dict, today_iso: str
) -> tuple[int, bool]:
    """Median minutes finished per day, over recent days that had any work.

    Days with nothing finished are ignored rather than counted as zero, so
    that days off do not drag the figure down.
    """
    earliest_day = shift_iso_date(today_iso, -DAYS_OF_HISTORY_TO_USE)

    minutes_by_day: dict[str, int] = defaultdict(int)
    for task in finished_tasks:
        if not task.completed_at:
            continue
        day_iso = task.completed_at[:10]
        if day_iso >= earliest_day:
            minutes_by_day[day_iso] += task.actual_minutes

    daily_totals = list(minutes_by_day.values())
    if len(daily_totals) < MINIMUM_DAYS_FOR_CAPACITY:
        return int(settings["assumed_daily_capacity_minutes"]), False
    return int(median(daily_totals)), True


def _clamp_multiplier(value: float) -> float:
    return max(SMALLEST_SENSIBLE_MULTIPLIER, min(LARGEST_SENSIBLE_MULTIPLIER, value))


def stalled_tasks(tasks: list[Task]) -> list[Task]:
    """Unfinished tasks deferred at least DEFERRALS_BEFORE_CONCERN times."""
    return sorted(
        (
            task
            for task in tasks
            if not task.is_done and task.times_deferred >= DEFERRALS_BEFORE_CONCERN
        ),
        key=lambda task: task.times_deferred,
        reverse=True,
    )
