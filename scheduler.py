"""
scheduler.py
============
Builds one day from a list of tasks.

    1. Cut the fixed commitments out of the working day, leaving free
       intervals.
    2. Select the tasks that belong to the day and sort them by urgency.
    3. Give every task that named a clock time its exact slot, and cut that
       out of the free intervals too.
    4. Place the deep-work tasks inside the deep-work window.
    5. Place the rest in the earliest interval that fits.
    6. Insert a break after each long stretch of unbroken work.
    7. Fill any spare room with undated tasks, stopping at measured capacity
       rather than at the end of the working day.

Tasks that find no slot are returned as unscheduled_tasks. Nothing here
touches disk.
"""

from __future__ import annotations

from models import (
    BLOCK_KIND_BREAK,
    BLOCK_KIND_FIXED,
    BLOCK_KIND_TASK,
    ENERGY_DEEP,
    DayPlan,
    ScheduledBlock,
    Task,
    clock_to_minutes,
)
from calibration import Calibration


def plan_day(
    all_tasks: list[Task],
    day_iso: str,
    today_iso: str,
    settings: dict,
    calibration: Calibration,
) -> DayPlan:
    """Builds the plan for one day. Changes nothing on disk."""
    free_intervals = build_free_intervals(settings)
    blocks = fixed_commitment_blocks(settings)

    chosen_tasks = select_tasks_for_day(all_tasks, day_iso)
    chosen_tasks.sort(key=lambda task: urgency_sort_key(task, day_iso))

    placed_task_ids: set[str] = set()
    minutes_since_last_break = 0
    planned_minutes = 0

    # -- pass 0: anything with a clock time gets exactly that slot ----------
    planned_minutes, clashes = place_fixed_time_tasks(
        chosen_tasks, blocks, free_intervals, placed_task_ids, planned_minutes
    )

    deep_window = (
        clock_to_minutes(settings["deep_work_starts_at"]),
        clock_to_minutes(settings["deep_work_ends_at"]),
    )

    # -- pass 1: deep work, inside the deep-work window only ----------------
    for task in chosen_tasks:
        if task.energy_level != ENERGY_DEEP or task.task_id in placed_task_ids:
            continue
        duration = calibration.realistic_minutes_for(task)
        slot = take_interval(free_intervals, duration, deep_window[0], deep_window[1])
        if slot is None:
            continue  # pass 2 retries it anywhere in the day
        blocks.append(block_for_task(task, slot))
        placed_task_ids.add(task.task_id)
        planned_minutes += duration
        minutes_since_last_break = add_break_if_needed(
            blocks, free_intervals, settings, minutes_since_last_break + duration, slot[1]
        )

    # -- pass 2: everything else, earliest gap that fits --------------------
    for task in chosen_tasks:
        if task.task_id in placed_task_ids:
            continue
        duration = calibration.realistic_minutes_for(task)
        slot = take_interval(free_intervals, duration)
        if slot is None:
            continue  # ends up in unscheduled below
        blocks.append(block_for_task(task, slot))
        placed_task_ids.add(task.task_id)
        planned_minutes += duration
        minutes_since_last_break = add_break_if_needed(
            blocks, free_intervals, settings, minutes_since_last_break + duration, slot[1]
        )

    # -- pass 3: fill leftover room with undated work, up to capacity ------
    if day_iso >= today_iso:
        planned_minutes, minutes_since_last_break = fill_spare_room(
            all_tasks,
            day_iso,
            blocks,
            free_intervals,
            placed_task_ids,
            planned_minutes,
            minutes_since_last_break,
            settings,
            calibration,
        )

    unscheduled = [
        task for task in chosen_tasks if task.task_id not in placed_task_ids
    ]
    overflow_minutes = sum(calibration.realistic_minutes_for(t) for t in unscheduled)

    blocks.sort(key=lambda block: block.start_minute)
    return DayPlan(
        day_iso=day_iso,
        blocks=blocks,
        unscheduled_tasks=unscheduled,
        planned_task_minutes=planned_minutes,
        overflow_minutes=overflow_minutes,
        clashes=clashes,
    )


# ---------------------------------------------------------------------------
# Tasks that named a clock time
# ---------------------------------------------------------------------------
def place_fixed_time_tasks(
    chosen_tasks: list[Task],
    blocks: list[ScheduledBlock],
    free_intervals: list[list[int]],
    placed_task_ids: set[str],
    planned_minutes: int,
) -> tuple[int, list[str]]:
    """Puts every timed task at its stated time, before anything else is placed.

    The estimation multiplier is not applied here. A dentist appointment at
    two o'clock lasts as long as it lasts; it is not a guess to be corrected.

    A time outside the working day still gets its block, so an evening
    appointment shows on the calendar instead of silently vanishing.
    """
    clashes: list[str] = []
    already_taken: list[tuple[int, int, str]] = []

    timed_tasks = sorted(
        (task for task in chosen_tasks if task.has_fixed_time()),
        key=lambda task: task.start_time,
    )

    for task in timed_tasks:
        start_minute = clock_to_minutes(task.start_time)
        end_minute = start_minute + task.estimated_minutes

        for other_start, other_end, other_title in already_taken:
            if start_minute < other_end and other_start < end_minute:
                clashes.append(f'"{task.title}" overlaps "{other_title}"')
                break

        reserve_exact_interval(free_intervals, start_minute, end_minute)
        blocks.append(block_for_task(task, (start_minute, end_minute)))
        already_taken.append((start_minute, end_minute, task.title))
        placed_task_ids.add(task.task_id)
        planned_minutes += task.estimated_minutes

    return planned_minutes, clashes


def reserve_exact_interval(
    free_intervals: list[list[int]], start_minute: int, end_minute: int
) -> None:
    """Cuts one exact stretch out of the free intervals, the way lunch is cut.

    Unlike take_interval this cannot fail: the time was named, so nothing else
    may be booked over it whether or not it sits inside the working day.
    """
    remaining: list[list[int]] = []
    for free_start, free_end in free_intervals:
        if end_minute <= free_start or start_minute >= free_end:
            remaining.append([free_start, free_end])
            continue
        if free_start < start_minute:
            remaining.append([free_start, start_minute])
        if end_minute < free_end:
            remaining.append([end_minute, free_end])
    free_intervals[:] = remaining


# ---------------------------------------------------------------------------
# Which tasks belong to this day
# ---------------------------------------------------------------------------
def select_tasks_for_day(all_tasks: list[Task], day_iso: str) -> list[Task]:
    """A task belongs to a day if it is pinned there, or unpinned and due by then.

    Tasks pinned to another day are never borrowed.
    """
    chosen = []
    for task in all_tasks:
        if task.is_done:
            continue
        if task.scheduled_date == day_iso:
            chosen.append(task)
        elif task.scheduled_date is None and task.is_due_on_or_before(day_iso):
            chosen.append(task)
    return chosen


def urgency_sort_key(task: Task, day_iso: str) -> tuple:
    """Overdue first, then by due date, then by priority, then oldest first."""
    is_overdue = task.due_date is not None and task.due_date < day_iso
    return (
        0 if is_overdue else 1,
        task.due_date_for_sorting(),
        task.priority,
        task.created_at,
    )


def fill_spare_room(
    all_tasks: list[Task],
    day_iso: str,
    blocks: list[ScheduledBlock],
    free_intervals: list[list[int]],
    placed_task_ids: set[str],
    planned_minutes: int,
    minutes_since_last_break: int,
    settings: dict,
    calibration: Calibration,
) -> tuple[int, int]:
    """Adds undated tasks, stopping at measured capacity.

    The limit is the capacity figure rather than the end of the working day,
    which is what keeps a full calendar from being an impossible one.
    """
    candidates = [
        task
        for task in all_tasks
        if not task.is_done
        and task.task_id not in placed_task_ids
        and task.due_date is None
        and task.scheduled_date is None
    ]
    candidates.sort(key=lambda task: (task.priority, task.created_at))

    for task in candidates:
        duration = calibration.realistic_minutes_for(task)
        if planned_minutes + duration > calibration.capacity_minutes:
            continue
        slot = take_interval(free_intervals, duration)
        if slot is None:
            break
        blocks.append(block_for_task(task, slot))
        placed_task_ids.add(task.task_id)
        planned_minutes += duration
        minutes_since_last_break = add_break_if_needed(
            blocks, free_intervals, settings, minutes_since_last_break + duration, slot[1]
        )
    return planned_minutes, minutes_since_last_break


# ---------------------------------------------------------------------------
# Free-interval bookkeeping. These two functions are the placement engine.
# ---------------------------------------------------------------------------
def build_free_intervals(settings: dict) -> list[list[int]]:
    """The working day minus every fixed commitment, as [start, end] pairs."""
    day_start = clock_to_minutes(settings["work_day_starts_at"])
    day_end = clock_to_minutes(settings["work_day_ends_at"])
    intervals = [[day_start, day_end]]

    for commitment in settings.get("fixed_commitments", []):
        busy_start = clock_to_minutes(commitment["start"])
        busy_end = clock_to_minutes(commitment["end"])
        remaining: list[list[int]] = []
        for free_start, free_end in intervals:
            if busy_end <= free_start or busy_start >= free_end:
                remaining.append([free_start, free_end])   # no overlap
                continue
            if free_start < busy_start:
                remaining.append([free_start, busy_start])  # piece before
            if busy_end < free_end:
                remaining.append([busy_end, free_end])      # piece after
        intervals = remaining

    intervals.sort()
    return intervals


def take_interval(
    free_intervals: list[list[int]],
    duration_minutes: int,
    window_start: int | None = None,
    window_end: int | None = None,
) -> tuple[int, int] | None:
    """Claims the earliest free stretch long enough, optionally within a window.

    Mutates free_intervals: the claimed minutes are removed and whatever is
    left of that interval goes back in its place.
    """
    for index, (gap_start, gap_end) in enumerate(free_intervals):
        earliest = gap_start if window_start is None else max(gap_start, window_start)
        latest = gap_end if window_end is None else min(gap_end, window_end)
        if latest - earliest < duration_minutes:
            continue

        claimed_start = earliest
        claimed_end = earliest + duration_minutes

        leftovers = []
        if gap_start < claimed_start:
            leftovers.append([gap_start, claimed_start])
        if claimed_end < gap_end:
            leftovers.append([claimed_end, gap_end])
        free_intervals[index:index + 1] = leftovers
        return (claimed_start, claimed_end)
    return None


def add_break_if_needed(
    blocks: list[ScheduledBlock],
    free_intervals: list[list[int]],
    settings: dict,
    minutes_since_last_break: int,
    after_minute: int,
) -> int:
    """Adds a break at after_minute once enough unbroken work has built up.

    Returns the new running total of unbroken work minutes.
    """
    if minutes_since_last_break < settings["minutes_between_breaks"]:
        return minutes_since_last_break

    break_length = settings["break_minutes"]
    slot = take_interval(
        free_intervals, break_length, after_minute, after_minute + break_length
    )
    if slot is None:
        return minutes_since_last_break  # no room here; retried after the next task

    blocks.append(
        ScheduledBlock(
            start_minute=slot[0],
            end_minute=slot[1],
            label="Break",
            kind=BLOCK_KIND_BREAK,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------
def block_for_task(task: Task, slot: tuple[int, int]) -> ScheduledBlock:
    return ScheduledBlock(
        start_minute=slot[0],
        end_minute=slot[1],
        label=task.title,
        kind=BLOCK_KIND_TASK,
        task_id=task.task_id,
        energy_level=task.energy_level,
    )


def fixed_commitment_blocks(settings: dict) -> list[ScheduledBlock]:
    return [
        ScheduledBlock(
            start_minute=clock_to_minutes(commitment["start"]),
            end_minute=clock_to_minutes(commitment["end"]),
            label=commitment.get("label", "Busy"),
            kind=BLOCK_KIND_FIXED,
        )
        for commitment in settings.get("fixed_commitments", [])
    ]
