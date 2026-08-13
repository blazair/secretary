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
    shift_iso_date,
)
from calibration import Calibration


def plan_day(
    all_tasks: list[Task],
    day_iso: str,
    today_iso: str,
    settings: dict,
    calibration: Calibration,
    pinned_sessions: list[dict] | None = None,
    remaining_by_task: dict[str, int] | None = None,
    allow_splitting: bool = True,
) -> DayPlan:
    """Builds the plan for one day. Changes nothing on disk.

    pinned_sessions are stretches the user fixed by dragging; they are
    reserved before anything else so that replanning cannot overwrite them.
    remaining_by_task carries the unfinished portion of a task forward from an
    earlier day, and is supplied by plan_range.
    """
    free_intervals = build_free_intervals(settings)
    blocks = fixed_commitment_blocks(settings)

    chosen_tasks = select_tasks_for_day(all_tasks, day_iso)
    chosen_tasks.sort(key=lambda task: urgency_sort_key(task, day_iso))

    # A task can now be partly placed, so membership is no longer expressive
    # enough and the bookkeeping counts minutes instead.
    placed_minutes: dict[str, int] = {}
    minutes_since_last_break = 0
    planned_minutes = 0

    def needed(task: Task) -> int:
        """How many minutes this day still owes a task."""
        if remaining_by_task is not None and task.task_id in remaining_by_task:
            return remaining_by_task[task.task_id]
        if task.has_fixed_time():
            return task.estimated_minutes
        return calibration.realistic_minutes_for(task)

    # -- pass -1: stretches the user pinned by dragging ---------------------
    planned_minutes = place_pinned_sessions(
        pinned_sessions or [], all_tasks, blocks, free_intervals,
        placed_minutes, planned_minutes,
    )

    # -- pass 0: anything with a clock time gets exactly that slot ----------
    planned_minutes, clashes = place_fixed_time_tasks(
        chosen_tasks, blocks, free_intervals, placed_minutes, planned_minutes
    )

    deep_window = (
        clock_to_minutes(settings["deep_work_starts_at"]),
        clock_to_minutes(settings["deep_work_ends_at"]),
    )

    # -- pass 1: deep work, inside the deep-work window only ----------------
    for task in chosen_tasks:
        if task.energy_level != ENERGY_DEEP:
            continue
        added, minutes_since_last_break = place_task(
            task, needed(task), free_intervals, blocks, placed_minutes,
            settings, minutes_since_last_break, allow_splitting,
            deep_window[0], deep_window[1],
        )
        planned_minutes += added  # anything left over is retried in pass 2

    # -- pass 2: everything else, earliest gap that fits --------------------
    for task in chosen_tasks:
        added, minutes_since_last_break = place_task(
            task, needed(task), free_intervals, blocks, placed_minutes,
            settings, minutes_since_last_break, allow_splitting,
        )
        planned_minutes += added

    # -- pass 3: fill leftover room with undated work, up to capacity ------
    backlog: list[Task] = []
    if day_iso >= today_iso:
        planned_minutes, minutes_since_last_break = fill_spare_room(
            all_tasks,
            day_iso,
            blocks,
            free_intervals,
            placed_minutes,
            planned_minutes,
            minutes_since_last_break,
            settings,
            calibration,
            backlog,
            allow_splitting,
        )

    unscheduled = []
    partially_placed: dict[str, tuple[int, int]] = {}
    overflow_minutes = 0
    for task in chosen_tasks:
        owed = needed(task)
        done = placed_minutes.get(task.task_id, 0)
        if done >= owed:
            continue
        unscheduled.append(task)
        overflow_minutes += owed - done
        if done > 0:
            partially_placed[task.task_id] = (done, owed)

    number_the_chunks(blocks)
    blocks.sort(key=lambda block: block.start_minute)
    return DayPlan(
        day_iso=day_iso,
        blocks=blocks,
        unscheduled_tasks=unscheduled,
        planned_task_minutes=planned_minutes,
        overflow_minutes=overflow_minutes,
        clashes=clashes,
        backlog=backlog,
        partially_placed=partially_placed,
    )


def plan_range(
    all_tasks: list[Task],
    start_iso: str,
    end_iso: str,
    today_iso: str,
    settings: dict,
    calibration: Calibration,
    pinned_by_day: dict[str, list[dict]] | None = None,
) -> dict[str, DayPlan]:
    """Plans consecutive days, carrying an unfinished remainder forward.

    Progress accumulates across the range, so a task that needs more minutes
    than one day holds is spread over several rather than being replanned in
    full each morning. Once its minutes are all accounted for it stops
    appearing.
    """
    pinned_by_day = pinned_by_day or {}
    total_needed: dict[str, int] = {}
    placed_so_far: dict[str, int] = {}
    plans: dict[str, DayPlan] = {}

    day_iso = start_iso
    while day_iso <= end_iso:
        remaining_by_task = {
            task_id: max(0, needed - placed_so_far.get(task_id, 0))
            for task_id, needed in total_needed.items()
        }
        plan = plan_day(
            all_tasks, day_iso, today_iso, settings, calibration,
            pinned_sessions=pinned_by_day.get(day_iso),
            remaining_by_task=remaining_by_task,
        )
        plans[day_iso] = plan

        # Record the full cost of tasks appearing for the first time today,
        # after planning, so that today saw the untouched estimate.
        for task in select_tasks_for_day(all_tasks, day_iso):
            if task.task_id not in total_needed:
                total_needed[task.task_id] = (
                    task.estimated_minutes if task.has_fixed_time()
                    else calibration.realistic_minutes_for(task)
                )

        for block in plan.blocks:
            if block.kind == BLOCK_KIND_TASK and block.task_id:
                placed_so_far[block.task_id] = (
                    placed_so_far.get(block.task_id, 0) + block.duration_minutes
                )

        day_iso = shift_iso_date(day_iso, 1)

    return plans


# ---------------------------------------------------------------------------
# Tasks that named a clock time
# ---------------------------------------------------------------------------
def place_fixed_time_tasks(
    chosen_tasks: list[Task],
    blocks: list[ScheduledBlock],
    free_intervals: list[list[int]],
    placed_minutes: dict[str, int],
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
        if placed_minutes.get(task.task_id, 0) >= task.estimated_minutes:
            continue  # a pinned session already covers it
        start_minute = clock_to_minutes(task.start_time)
        end_minute = start_minute + task.estimated_minutes

        for other_start, other_end, other_title in already_taken:
            if start_minute < other_end and other_start < end_minute:
                clashes.append(f'"{task.title}" overlaps "{other_title}"')
                break

        reserve_exact_interval(free_intervals, start_minute, end_minute)
        blocks.append(block_for_task(task, (start_minute, end_minute)))
        already_taken.append((start_minute, end_minute, task.title))
        placed_minutes[task.task_id] = task.estimated_minutes
        planned_minutes += task.estimated_minutes

    return planned_minutes, clashes


def place_pinned_sessions(
    pinned_sessions: list[dict],
    all_tasks: list[Task],
    blocks: list[ScheduledBlock],
    free_intervals: list[list[int]],
    placed_minutes: dict[str, int],
    planned_minutes: int,
) -> int:
    """Reserves the stretches the user dragged, before anything else is placed.

    Without this pass every replan would discard the user's own arrangement.
    """
    tasks_by_id = {task.task_id: task for task in all_tasks}

    for session in sorted(pinned_sessions, key=lambda s: s["start_minute"]):
        task = tasks_by_id.get(str(session["task_id"]))
        if task is None or task.is_done:
            continue
        start_minute = session["start_minute"]
        end_minute = session["end_minute"]
        reserve_exact_interval(free_intervals, start_minute, end_minute)
        blocks.append(
            block_for_task(
                task, (start_minute, end_minute),
                session_id=session.get("id"), origin="pinned",
            )
        )
        placed_minutes[task.task_id] = (
            placed_minutes.get(task.task_id, 0) + end_minute - start_minute
        )
        planned_minutes += end_minute - start_minute

    return planned_minutes


def place_task(
    task: Task,
    needed_minutes: int,
    free_intervals: list[list[int]],
    blocks: list[ScheduledBlock],
    placed_minutes: dict[str, int],
    settings: dict,
    minutes_since_last_break: int,
    allow_splitting: bool,
    window_start: int | None = None,
    window_end: int | None = None,
) -> tuple[int, int]:
    """Places whatever of a task still needs placing, splitting when it must.

    Returns the minutes placed and the running unbroken-work total.
    """
    already_placed = placed_minutes.get(task.task_id, 0)
    remaining = needed_minutes - already_placed
    if remaining <= 0:
        return 0, minutes_since_last_break

    if not (allow_splitting and task.is_splittable):
        slot = take_interval(free_intervals, remaining, window_start, window_end)
        if slot is None:
            return 0, minutes_since_last_break
        blocks.append(block_for_task(task, slot))
        placed_minutes[task.task_id] = already_placed + remaining
        minutes_since_last_break = add_break_if_needed(
            blocks, free_intervals, settings,
            minutes_since_last_break + remaining, slot[1],
        )
        return remaining, minutes_since_last_break

    minutes_added = 0
    while remaining > 0:
        slot = take_next_chunk(
            free_intervals, remaining, task.min_session_minutes,
            task.max_session_minutes, window_start, window_end,
        )
        if slot is None:
            break
        chunk = slot[1] - slot[0]
        blocks.append(block_for_task(task, slot))
        minutes_added += chunk
        remaining -= chunk
        # Carved per chunk, and before the next chunk picks its spot, so a
        # task split three ways earns three breaks.
        minutes_since_last_break = add_break_if_needed(
            blocks, free_intervals, settings,
            minutes_since_last_break + chunk, slot[1],
        )

    if minutes_added:
        placed_minutes[task.task_id] = already_placed + minutes_added
    return minutes_added, minutes_since_last_break


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
    placed_minutes: dict[str, int],
    planned_minutes: int,
    minutes_since_last_break: int,
    settings: dict,
    calibration: Calibration,
    backlog: list[Task],
    allow_splitting: bool = True,
) -> tuple[int, int]:
    """Adds undated tasks, stopping at measured capacity.

    The limit is the capacity figure rather than the end of the working day,
    which is what keeps a full calendar from being an impossible one.

    Anything that finds no room joins the backlog, so an undated task can no
    longer disappear without being reported.
    """
    candidates = [
        task
        for task in all_tasks
        if not task.is_done
        and task.task_id not in placed_minutes
        and task.due_date is None
        and task.scheduled_date is None
    ]
    candidates.sort(key=lambda task: (task.priority, task.created_at))

    for task in candidates:
        duration = calibration.realistic_minutes_for(task)
        if planned_minutes + duration > calibration.capacity_minutes:
            backlog.append(task)
            continue
        added, minutes_since_last_break = place_task(
            task, duration, free_intervals, blocks, placed_minutes,
            settings, minutes_since_last_break, allow_splitting,
        )
        planned_minutes += added
        if added < duration:
            # A partly placed filler still counts as unfinished business.
            backlog.append(task)
        # A task that did not fit must not stop the ones queued behind it.
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


def take_next_chunk(
    free_intervals: list[list[int]],
    remaining_minutes: int,
    min_chunk: int = 25,
    max_chunk: int | None = None,
    window_start: int | None = None,
    window_end: int | None = None,
) -> tuple[int, int] | None:
    """Claims one stretch towards a task that needs more than one sitting.

    The sibling of take_interval, and the fix for the single-gap ceiling: a
    task longer than the largest free stretch could previously never be
    scheduled at all.

    One chunk at a time rather than all of them at once, so the caller can
    carve a break out of the free intervals before the next chunk chooses
    where to sit.
    """
    for index, (gap_start, gap_end) in enumerate(free_intervals):
        earliest = gap_start if window_start is None else max(gap_start, window_start)
        latest = gap_end if window_end is None else min(gap_end, window_end)
        usable = latest - earliest
        if usable <= 0:
            continue

        take = min(remaining_minutes, usable)
        if max_chunk:
            take = min(take, max_chunk)

        # Orphan tail: leaving 3 minutes over is worse than taking 3 fewer now.
        tail = remaining_minutes - take
        if 0 < tail < min_chunk and take - (min_chunk - tail) >= min_chunk:
            take -= min_chunk - tail

        # Sliver: a shard smaller than a usable session is noise, unless it
        # happens to finish the task.
        if take < min_chunk and take < remaining_minutes:
            continue

        claimed_start = earliest
        claimed_end = earliest + take

        leftovers = []
        if gap_start < claimed_start:
            leftovers.append([gap_start, claimed_start])
        if claimed_end < gap_end:
            leftovers.append([claimed_end, gap_end])
        free_intervals[index:index + 1] = leftovers
        return (claimed_start, claimed_end)

    return None


def number_the_chunks(blocks: list[ScheduledBlock]) -> None:
    """Labels a split task's blocks 1 of 3, 2 of 3, and so on."""
    by_task: dict[str, list[ScheduledBlock]] = {}
    for block in blocks:
        if block.kind == BLOCK_KIND_TASK and block.task_id:
            by_task.setdefault(block.task_id, []).append(block)

    for chunks in by_task.values():
        chunks.sort(key=lambda block: block.start_minute)
        for position, block in enumerate(chunks, start=1):
            block.sequence = position
            block.of = len(chunks)


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
def block_for_task(
    task: Task,
    slot: tuple[int, int],
    session_id: int | None = None,
    origin: str = "auto",
) -> ScheduledBlock:
    return ScheduledBlock(
        start_minute=slot[0],
        end_minute=slot[1],
        label=task.title,
        kind=BLOCK_KIND_TASK,
        task_id=task.task_id,
        energy_level=task.energy_level,
        session_id=session_id,
        origin=origin,
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
