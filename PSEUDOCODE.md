# Pseudocode

The app module by module, in the order worth reading. The real code follows
this structure function for function.

Core idea: tasks have durations, the user has a capacity, the scheduler fits
one into the other and reports the gap.

---

## models.py — the vocabulary

```
TASK is:
    id, title
    estimated_minutes          the stated estimate
    energy_level               light | normal | deep
    priority                   1 must | 2 should | 3 maybe
    category                   #writing, #admin - what calibration groups by
    due_date                   the day it must be finished by
    scheduled_date             the day it is pinned to, if any
    start_time                 "14:00" if a clock time was given
    created_at
    is_done, completed_at
    actual_minutes             how long it really took
    times_deferred             how many times it has been pushed back
    note

SCHEDULED_BLOCK is:
    start_minute, end_minute   minutes since midnight, so all maths is integers
    label, kind                task | break | fixed
    task_id, energy_level

DAY_PLAN is:
    the day, its blocks, the tasks that did not fit,
    minutes planned, minutes overflowed, any clashes

HELPERS:
    clock_to_minutes("09:30") -> 570
    minutes_to_clock(570)     -> "09:30"
    minutes_to_human(95)      -> "1h 35m"
    friendly_day_name(day)    -> "Today" / "Tomorrow" / "Tue 12 Aug"
```

---

## quick_add.py — one text box

```
PARSE the typed line word by word:
    "at" or "@" followed by a time -> swallow both, keep the time
    looks like 2pm / 2:30pm / 14:00 -> fixed clock time
    looks like 90m / 2h / 45min     -> duration
    looks like !1 !2 !3             -> priority
    looks like #word                -> category
    looks like ~light|normal|deep   -> energy
    looks like today / tomorrow /
       mon..sun / +3d / 2026-08-12  -> due date
    anything else                   -> part of the title

    a clock time needs am/pm or a colon, so a bare "2" stays in the title
    and "meet at the place" keeps its "at"

    a time with no date means today
    no duration means 30 minutes, and it says so

RETURN the task, plus what was recognised
    ("read as: 90m, #writing, priority 1")
```

Matched tokens are always removed from the line. Where two of a kind appear,
the last one wins.

---

## storage.py — four files on disk

```
data/tasks.json      current state of every task, rewritten on each save
data/events.jsonl    append-only history, one JSON object per line
data/notes.json      one note per day
data/settings.json   working hours, deep-work window, fixed commitments

RECORD_EVENT(type, details):
    append {timestamp, type, ...details} as one line to events.jsonl
```

`events.jsonl` has no reader yet. Later versions learn from history, so
collection starts now.

---

## calibration.py — measuring the user's pace

```
BUILD_CALIBRATION(all tasks):

    finished = tasks that are done and have actual_minutes

    # 1. how far off the estimates are
    for each finished task:
        ratio = actual_minutes / estimated_minutes
        file it under its category, and in the overall pile

    overall_multiplier   = median(all ratios)         if >= 3 samples else 1.0
    multiplier[category] = median(that category)      if >= 3 samples
    clamp everything to 0.5 .. 3.0

    # 2. how much gets finished in a day
    group finished tasks by the day they were completed
    sum actual_minutes per day
    skip days with nothing finished
    capacity = median(daily totals) over 21 days      if >= 3 such days
             else the assumed number from settings

REALISTIC_MINUTES_FOR(task):
    task.estimated_minutes * multiplier[task.category]

STALLED_TASKS:
    unfinished tasks pushed back 3 or more times
```

Medians are used so one unusually long task cannot skew the result.

---

## scheduler.py — tasks in, a day out

```
BUILD_FREE_INTERVALS(settings):
    start with [work_day_start .. work_day_end]
    for each fixed commitment (lunch, standing meeting):
        cut it out, leaving the piece before and the piece after
    -> a list of free [start, end] stretches

TAKE_INTERVAL(free, duration, optional window):
    walk the free stretches in time order
    find the first with room, inside the window if one was given
    claim `duration` minutes from its start
    put the leftover back in its place
    -> the claimed (start, end), or nothing


PLAN_DAY(all tasks, the day):

    free   = BUILD_FREE_INTERVALS
    blocks = the fixed commitments themselves

    SELECT the tasks for this day:
        pinned to this day                        -> yes
        pinned to a different day                 -> no
        unpinned and due on or before this day    -> yes

    SORT: overdue first, then due date, then priority, then oldest

    PASS 0 - tasks with a clock time
        book each at exactly that time, for exactly the length given
        no multiplier is applied
        cut the stretch out of free, the way lunch is cut
        overlapping two timed tasks records a clash and draws both
        a time outside working hours still gets a block

    PASS 1 - deep work, inside the deep-work window only
        slot = TAKE_INTERVAL(free, realistic_minutes, deep window)
        if it fits, place it; if not, leave it for pass 2

    PASS 2 - everything else, earliest gap that fits
        slot = TAKE_INTERVAL(free, realistic_minutes)
        if it fits, place it; if not, it overflows

    AFTER EACH PLACEMENT:
        add up unbroken working minutes
        past `minutes_between_breaks`, carve a break straight after
        and reset the counter

    PASS 3 - fill leftover room up to capacity
        for undated, unpinned tasks in priority order:
            if planned + this task > capacity, skip it
            otherwise place it

    RETURN blocks, overflow list, minute totals, clashes
```

Pass 3 stops at capacity rather than at the end of the working day.

---

## capacity.py — arithmetic into a sentence

```
CAPACITY_REPORT:
    intended = minutes placed + minutes overflowed
    capacity = what calibration measured
    load     = intended / capacity

SUMMARISE_DAY:
    load <= 0.50   "Light: 2h 10m against a usual 4h 10m. Room for 2h more."
    load <= 0.85   "Reasonable: 3h 20m of a usual 4h 10m."
    load <= 1.00   "Full: 4h 5m of a usual 4h 10m. No slack left."
    load <= 1.25   "Slightly over. Something will slip."
    otherwise      "Overbooked by 2h 30m."

    if over capacity:       name the cheapest task to move
                            (lowest priority, latest deadline, biggest)
    if two timed tasks overlap: say they are double booked
    if anything overflowed: say how many did not fit
    if anything is stalled: "X has been pushed back 5 times."

REACT_TO_NEW_TASK(the task just added):
    say what was booked, and if it exceeds the typed estimate, why:
        "estimate was 60m, but #writing runs 1.75x"
    if this task tipped the day over, say so with both numbers
    if the day was already over, say by how much
    if it did not fit, say that
    otherwise just confirm
```

Messages name the specific task to move.

---

## ui.py — the window

```
LAYOUT
    top     one text box (quick add) + the syntax hint
    left    the task list, colour coded:
                red    = overdue
                orange = did not fit in this day
                purple = pushed back 3+ times
                grey   = finished
    right   the day as a calendar, coloured by energy,
            with a red line at the current time
    bottom  the current status sentence

    optional panels behind flags: notes box, load meter,
    calibration line, extra buttons

REFRESH_EVERYTHING - the single code path, always in this order:
    calibration = measure from the task list
    day_plan    = plan the viewed day using that calibration
    report      = the capacity arithmetic
    then redraw list, calendar, status

ON ADD:        parse -> save -> log event -> replan -> react
ON DONE:       ask how long it really took, the only source of
               calibration data -> save -> log -> replan
ON PUSH:       scheduled_date += 1 day, times_deferred += 1, log it
ON PIN/UNPIN:  promise or release a specific day
ON DELETE:     confirm, then log the deletion with its deferral count
```

Scheduling and calibration logic stay out of this file.

---

## Roadmap

- **v1** (this) — plan the day, log everything
- **v1.1** — edit a task after adding it
- **v2** — per-time-of-day capacity, estimates suggested from similar past tasks
- **v3** — recurring tasks, `.ics` import and export
- **v4** — language layer, weekly review written from `events.jsonl`
- **v5** — morning plan, evening review, notifications

v4 needs the capacity numbers underneath it, which is why it comes after v2.
