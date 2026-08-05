"""
ui.py
=====
The tkinter window.

    top     the quick-add box, which is the only way tasks are created
    left    the task list
    right   the day drawn as a calendar
    bottom  the current status message

Collects input, calls the other modules and draws what they return. No
scheduling or calibration logic lives here.

Several panels are switched off by the flags below. Their code is still
present and still works; setting a flag to True puts the panel back.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import storage
from calibration import build_calibration
from capacity import build_capacity_report, react_to_new_task, summarise_day
from models import (
    BLOCK_KIND_BREAK,
    BLOCK_KIND_FIXED,
    ENERGY_DEEP,
    ENERGY_LIGHT,
    ENERGY_NORMAL,
    PRIORITY_LABELS,
    Task,
    clock_to_minutes,
    friendly_day_name,
    minutes_to_clock,
    minutes_to_human,
    now_as_text,
    shift_iso_date,
    today_as_iso,
)
from quick_add import QUICK_ADD_HINT, parse_quick_add
from scheduler import plan_day

# fill colour, border colour - one pair per kind of block
BLOCK_COLOURS = {
    ENERGY_DEEP: ("#dbe4ff", "#3b5bdb"),
    ENERGY_NORMAL: ("#e3fafc", "#0c8599"),
    ENERGY_LIGHT: ("#f1f3f5", "#868e96"),
    BLOCK_KIND_BREAK: ("#fff9db", "#f08c00"),
    BLOCK_KIND_FIXED: ("#e9ecef", "#495057"),
}

TIME_GUTTER_WIDTH = 46
CANVAS_MARGIN = 10

# ---------------------------------------------------------------------------
# Which panels are shown. Set one to True to bring that panel back; the code
# behind each of them is untouched.
# ---------------------------------------------------------------------------
SHOW_NOTES_PANEL = False        # the free-text note for the viewed day
SHOW_LOAD_METER = False         # the bar showing how full the day is
SHOW_CALIBRATION_LINE = False   # the grey line describing the measured numbers
SHOW_EXTRA_BUTTONS = False      # push to tomorrow, pin to this day, unpin


class AssistantApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Secretary")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)

        # -- current state -------------------------------------------------
        self.settings = storage.load_settings()
        self.tasks: list[Task] = storage.load_tasks()
        self.today_iso = today_as_iso()
        self.viewed_day_iso = self.today_iso
        self.show_finished = tk.BooleanVar(value=False)

        # Recomputed on every refresh, never edited directly.
        self.calibration = None
        self.day_plan = None
        self.capacity_report = None

        # Widgets belonging to switched-off panels stay None.
        self.load_meter = None
        self.note_text = None
        self.calibration_label = None

        self._build_layout()
        self.refresh_everything()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)

    # =====================================================================
    # Building the window
    # =====================================================================
    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(1, weight=1)

        self._build_quick_add_bar()
        self._build_task_list()
        self._build_day_view()
        self._build_status_bar()

    def _build_quick_add_bar(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.quick_add_entry = ttk.Entry(frame, font=("Segoe UI", 11))
        self.quick_add_entry.grid(row=0, column=0, sticky="ew")
        self.quick_add_entry.bind("<Return>", lambda event: self.on_add_task())
        self.quick_add_entry.focus_set()

        ttk.Button(frame, text="Add", command=self.on_add_task).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Label(
            frame, text=QUICK_ADD_HINT, foreground="#868e96", font=("Segoe UI", 8)
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

    def _build_task_list(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Tasks", padding=8)
        frame.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=5)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("title", "due", "estimate", "energy", "priority", "pushed")
        self.task_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for column, heading, width in (
            ("title", "Task", 300),
            ("due", "Due", 90),
            ("estimate", "Est.", 60),
            ("energy", "Energy", 70),
            ("priority", "Priority", 70),
            ("pushed", "Pushed", 60),
        ):
            self.task_tree.heading(column, text=heading)
            self.task_tree.column(column, width=width, anchor="w")
        self.task_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Row colours, so the list does not need a status column.
        self.task_tree.tag_configure("overdue", foreground="#c92a2a")
        self.task_tree.tag_configure("did_not_fit", foreground="#e8590c")
        self.task_tree.tag_configure("finished", foreground="#adb5bd")
        self.task_tree.tag_configure("stalled", foreground="#862e9c")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        button_definitions = [("Done", self.on_mark_done)]
        if SHOW_EXTRA_BUTTONS:
            button_definitions += [
                ("Push to tomorrow", self.on_defer_to_tomorrow),
                ("Pin to this day", self.on_pin_to_viewed_day),
                ("Unpin", self.on_unpin),
            ]
        button_definitions.append(("Delete", self.on_delete_task))

        for text, command in button_definitions:
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(
            buttons,
            text="show finished",
            variable=self.show_finished,
            command=self.refresh_everything,
        ).pack(side="right")

    def _build_day_view(self) -> None:
        frame = ttk.Frame(self.root, padding=(5, 5, 10, 5))
        frame.grid(row=1, column=1, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=3)          # the calendar takes the space
        if SHOW_NOTES_PANEL:
            frame.rowconfigure(3, weight=1)

        navigation = ttk.Frame(frame)
        navigation.grid(row=0, column=0, sticky="ew")
        navigation.columnconfigure(1, weight=1)
        ttk.Button(navigation, text="<", width=3, command=self.on_previous_day).grid(row=0, column=0)
        self.day_label = ttk.Label(navigation, text="", font=("Segoe UI", 11, "bold"), anchor="center")
        self.day_label.grid(row=0, column=1, sticky="ew")
        ttk.Button(navigation, text=">", width=3, command=self.on_next_day).grid(row=0, column=2)
        ttk.Button(navigation, text="Today", command=self.on_jump_to_today).grid(row=0, column=3, padx=(6, 0))

        self.calendar_canvas = tk.Canvas(frame, background="white", highlightthickness=1,
                                         highlightbackground="#dee2e6")
        self.calendar_canvas.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        self.calendar_canvas.bind("<Configure>", lambda event: self.draw_calendar())

        if SHOW_LOAD_METER:
            self.load_meter = ttk.Progressbar(frame, maximum=100)
            self.load_meter.grid(row=2, column=0, sticky="ew")

        if SHOW_NOTES_PANEL:
            notes_frame = ttk.LabelFrame(frame, text="Note for this day", padding=6)
            notes_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
            notes_frame.columnconfigure(0, weight=1)
            notes_frame.rowconfigure(0, weight=1)
            self.note_text = tk.Text(notes_frame, height=5, wrap="word", font=("Segoe UI", 10))
            self.note_text.grid(row=0, column=0, sticky="nsew")
            ttk.Button(notes_frame, text="Save note", command=self.save_current_note).grid(
                row=1, column=0, sticky="e", pady=(4, 0)
            )

    def _build_status_bar(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(
            frame, text="", font=("Segoe UI", 10), wraplength=1120, justify="left"
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        if SHOW_CALIBRATION_LINE:
            self.calibration_label = ttk.Label(
                frame, text="", font=("Segoe UI", 8), foreground="#868e96", wraplength=1120
            )
            self.calibration_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

    # =====================================================================
    # Refreshing - the single path, always in this order
    # =====================================================================
    def refresh_everything(self, status_message: str | None = None) -> None:
        """Recomputes calibration, plan and report, then redraws everything."""
        self.today_iso = today_as_iso()
        self.calibration = build_calibration(self.tasks, self.settings, self.today_iso)
        self.day_plan = plan_day(
            self.tasks, self.viewed_day_iso, self.today_iso, self.settings, self.calibration
        )
        self.capacity_report = build_capacity_report(self.day_plan, self.calibration)

        self.day_label.configure(
            text=f"{friendly_day_name(self.viewed_day_iso, self.today_iso)}  -  {self.viewed_day_iso}"
        )
        self.draw_task_list()
        self.draw_calendar()
        self.draw_load_meter()
        self.load_current_note()

        self.status_label.configure(
            text=status_message
            or summarise_day(self.capacity_report, self.day_plan, self.tasks, self.today_iso)
        )
        if self.calibration_label is not None:
            self.calibration_label.configure(text=self.calibration.describe())

    def draw_task_list(self) -> None:
        selected_id = self.selected_task_id()
        self.task_tree.delete(*self.task_tree.get_children())

        did_not_fit_ids = {task.task_id for task in self.day_plan.unscheduled_tasks}

        for task in sorted(self.tasks, key=self._task_list_sort_key):
            if task.is_done and not self.show_finished.get():
                continue
            self.task_tree.insert(
                "",
                "end",
                iid=task.task_id,
                values=(
                    ("[x] " if task.is_done else "") + task.title,
                    self._due_column_text(task),
                    f"{task.estimated_minutes}m",
                    task.energy_level,
                    PRIORITY_LABELS.get(task.priority, "?"),
                    task.times_deferred or "",
                ),
                tags=self._row_tags(task, did_not_fit_ids),
            )

        if selected_id and self.task_tree.exists(selected_id):
            self.task_tree.selection_set(selected_id)

    def _task_list_sort_key(self, task: Task) -> tuple:
        return (task.is_done, task.due_date_for_sorting(), task.priority, task.created_at)

    def _due_column_text(self, task: Task) -> str:
        if task.scheduled_date:
            day_text = "pinned " + friendly_day_name(task.scheduled_date, self.today_iso).lower()
        elif task.due_date:
            day_text = friendly_day_name(task.due_date, self.today_iso)
        else:
            return "-"
        if task.start_time:
            return f"{day_text} {task.start_time}"
        return day_text

    def _row_tags(self, task: Task, did_not_fit_ids: set[str]) -> tuple:
        if task.is_done:
            return ("finished",)
        if task.is_overdue(self.today_iso):
            return ("overdue",)
        if task.task_id in did_not_fit_ids:
            return ("did_not_fit",)
        if task.times_deferred >= 3:
            return ("stalled",)
        return ()

    # ---------------------------------------------------------------------
    # The calendar drawing
    # ---------------------------------------------------------------------
    def draw_calendar(self) -> None:
        canvas = self.calendar_canvas
        canvas.delete("all")

        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()
        if canvas_width < 50 or canvas_height < 50:
            return  # window not laid out yet

        day_start = clock_to_minutes(self.settings["work_day_starts_at"])
        day_end = clock_to_minutes(self.settings["work_day_ends_at"])

        # A task given a clock time may sit outside working hours, so stretch
        # the view to cover it rather than drawing it off the edge.
        for block in self.day_plan.blocks:
            day_start = min(day_start, block.start_minute)
            day_end = max(day_end, block.end_minute)
        day_start = (day_start // 60) * 60          # start the view on the hour
        minutes_in_view = max(1, day_end - day_start)

        top = CANVAS_MARGIN
        bottom = canvas_height - CANVAS_MARGIN
        pixels_per_minute = (bottom - top) / minutes_in_view

        def y_for(minute: int) -> float:
            return top + (minute - day_start) * pixels_per_minute

        left = TIME_GUTTER_WIDTH
        right = canvas_width - CANVAS_MARGIN

        # hour gridlines and their labels
        first_hour = (day_start // 60) * 60
        for minute in range(first_hour, day_end + 1, 60):
            if minute < day_start:
                continue
            y = y_for(minute)
            canvas.create_line(left, y, right, y, fill="#f1f3f5")
            canvas.create_text(
                left - 6, y, text=minutes_to_clock(minute), anchor="e",
                fill="#adb5bd", font=("Segoe UI", 8),
            )

        # the blocks themselves
        for block in self.day_plan.blocks:
            fill, border = BLOCK_COLOURS.get(
                block.kind if block.kind != "task" else block.energy_level,
                BLOCK_COLOURS[ENERGY_NORMAL],
            )
            top_y = y_for(block.start_minute)
            bottom_y = max(y_for(block.end_minute), top_y + 14)
            canvas.create_rectangle(left + 2, top_y, right, bottom_y, fill=fill, outline=border)
            canvas.create_text(
                left + 8,
                top_y + 3,
                text=f"{block.time_range_text()}  {block.label}",
                anchor="nw",
                font=("Segoe UI", 8),
                fill="#212529",
                width=max(40, right - left - 14),
            )

        # a line marking the current time
        if self.viewed_day_iso == self.today_iso:
            import datetime as _datetime

            now = _datetime.datetime.now()
            minute_now = now.hour * 60 + now.minute
            if day_start <= minute_now <= day_end:
                y = y_for(minute_now)
                canvas.create_line(left, y, right, y, fill="#fa5252", width=2)

        # anything that could not be placed, listed at the foot of the day
        if self.day_plan.unscheduled_tasks:
            names = ", ".join(task.title for task in self.day_plan.unscheduled_tasks[:3])
            canvas.create_text(
                left + 4, bottom - 4,
                text=f"did not fit: {names}",
                anchor="sw", fill="#e8590c", font=("Segoe UI", 8),
            )

    def draw_load_meter(self) -> None:
        if self.load_meter is None:
            return
        percentage = min(100, int(self.capacity_report.load_ratio * 100))
        self.load_meter.configure(value=percentage)

    # =====================================================================
    # Actions
    # =====================================================================
    def on_add_task(self) -> None:
        typed_text = self.quick_add_entry.get().strip()
        if not typed_text:
            return

        report_before = self.capacity_report
        new_task, understood = parse_quick_add(typed_text)

        self.tasks.append(new_task)
        storage.save_tasks(self.tasks)
        storage.record_event(
            "task_created",
            {"task_id": new_task.task_id, "title": new_task.title,
             "estimated_minutes": new_task.estimated_minutes,
             "category": new_task.category, "raw_input": typed_text},
        )

        self.quick_add_entry.delete(0, "end")
        self.refresh_everything()

        message = react_to_new_task(
            new_task, report_before, self.capacity_report, self.day_plan,
            self.calibration, self.tasks, self.today_iso,
        )
        self.status_label.configure(text=message + "   [read as: " + ", ".join(understood) + "]")

    def on_mark_done(self) -> None:
        task = self.selected_task()
        if task is None or task.is_done:
            return

        actual_minutes = simpledialog.askinteger(
            "How long did it really take?",
            f'"{task.title}"\n\nEstimated at {task.estimated_minutes} minutes.\n'
            f"Actual minutes (this is the only calibration input):",
            initialvalue=task.estimated_minutes,
            minvalue=1,
            maxvalue=24 * 60,
            parent=self.root,
        )
        if actual_minutes is None:
            return  # cancelled; leave the task untouched rather than half done

        task.is_done = True
        task.completed_at = now_as_text()
        task.actual_minutes = actual_minutes
        storage.save_tasks(self.tasks)
        storage.record_event(
            "task_completed",
            {"task_id": task.task_id, "title": task.title,
             "estimated_minutes": task.estimated_minutes,
             "actual_minutes": actual_minutes, "category": task.category},
        )

        difference = actual_minutes - task.estimated_minutes
        if difference > 0:
            verdict = f"{minutes_to_human(difference)} longer than estimated."
        elif difference < 0:
            verdict = f"{minutes_to_human(-difference)} faster than estimated."
        else:
            verdict = "Exactly as estimated."
        self.refresh_everything(status_message=f'Done: "{task.title}". {verdict}')

    def on_defer_to_tomorrow(self) -> None:
        task = self.selected_task()
        if task is None or task.is_done:
            return

        starting_point = task.scheduled_date or task.due_date or self.today_iso
        task.scheduled_date = shift_iso_date(max(starting_point, self.today_iso), 1)
        task.times_deferred += 1
        storage.save_tasks(self.tasks)
        storage.record_event(
            "task_deferred",
            {"task_id": task.task_id, "title": task.title,
             "moved_to": task.scheduled_date, "times_deferred": task.times_deferred},
        )

        note = ""
        if task.times_deferred >= 3:
            note = (
                f" That is {task.times_deferred} times now. "
                f"A repeatedly moved task usually signals an unmade decision."
            )
        self.refresh_everything(
            status_message=f'"{task.title}" moved to {task.scheduled_date}.{note}'
        )

    def on_pin_to_viewed_day(self) -> None:
        task = self.selected_task()
        if task is None or task.is_done:
            return
        task.scheduled_date = self.viewed_day_iso
        storage.save_tasks(self.tasks)
        storage.record_event(
            "task_pinned", {"task_id": task.task_id, "day": self.viewed_day_iso}
        )
        self.refresh_everything(
            status_message=f'"{task.title}" pinned to {self.viewed_day_iso}.'
        )

    def on_unpin(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        task.scheduled_date = None
        storage.save_tasks(self.tasks)
        storage.record_event("task_unpinned", {"task_id": task.task_id})
        self.refresh_everything(
            status_message=f'"{task.title}" unpinned - it will be scheduled wherever it fits.'
        )

    def on_delete_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if not messagebox.askyesno("Delete", f'Delete "{task.title}"?', parent=self.root):
            return
        self.tasks = [other for other in self.tasks if other.task_id != task.task_id]
        storage.save_tasks(self.tasks)
        storage.record_event(
            "task_deleted", {"task_id": task.task_id, "title": task.title,
                             "times_deferred": task.times_deferred}
        )
        self.refresh_everything(status_message=f'Deleted "{task.title}".')

    # -- moving between days ----------------------------------------------
    def on_previous_day(self) -> None:
        self.save_current_note()
        self.viewed_day_iso = shift_iso_date(self.viewed_day_iso, -1)
        self.refresh_everything()

    def on_next_day(self) -> None:
        self.save_current_note()
        self.viewed_day_iso = shift_iso_date(self.viewed_day_iso, 1)
        self.refresh_everything()

    def on_jump_to_today(self) -> None:
        self.save_current_note()
        self.viewed_day_iso = today_as_iso()
        self.refresh_everything()

    # -- notes -------------------------------------------------------------
    def load_current_note(self) -> None:
        if self.note_text is None:
            return
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", storage.get_note_for_day(self.viewed_day_iso))

    def save_current_note(self) -> None:
        if self.note_text is None:
            return
        storage.save_note_for_day(self.viewed_day_iso, self.note_text.get("1.0", "end").strip())

    def on_close_window(self) -> None:
        self.save_current_note()
        storage.save_tasks(self.tasks)
        self.root.destroy()

    # -- selection helpers -------------------------------------------------
    def selected_task_id(self) -> str | None:
        selection = self.task_tree.selection()
        return selection[0] if selection else None

    def selected_task(self) -> Task | None:
        task_id = self.selected_task_id()
        if task_id is None:
            self.status_label.configure(text="Select a task first.")
            return None
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None


def run_application() -> None:
    storage.ensure_data_directory()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")  # nicer on Windows, ignored elsewhere
    except tk.TclError:
        pass
    AssistantApp(root)
    root.mainloop()
