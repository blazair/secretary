// main.js — the controller. Holds what is on screen, asks the server for
// truth, and redraws whole containers from it.

import { api } from "./api.js";
import { $, $$, clear, el, humanMinutes, isoToday, mondayOf, shiftIso } from "./dom.js";
import { dateHelpers, renderDay, renderMonth, renderWeek, wireCalendarInteractions } from "./views.js";

const state = {
  user: null,
  view: "day",
  date: isoToday(),
  tasks: [],
  selectedTaskId: null,
  day: null,
  timerTick: null,
};

// ------------------------------------------------------------------ gate --
let gateMode = "login";

function showGate(message = "") {
  $("#gate").classList.remove("hidden");
  $("#app").classList.add("hidden");
  $("#gate-error").textContent = message;
}

function setGateMode(mode) {
  gateMode = mode;
  const creating = mode === "register";
  $("#gate-submit").textContent = creating ? "Create account" : "Sign in";
  $("#gate-swap").textContent = creating ? "I already have an account" : "Create an account";
  $("#invite-field").classList.toggle("hidden", !creating);
  $("#gate-line").textContent = creating
    ? "The first account on this instance needs no invite code. After that one is required."
    : "A to-do list that builds a calendar from itself, then reports whether the day actually fits.";
  $("#gate-error").textContent = "";
}

$("#gate-swap").addEventListener("click", () =>
  setGateMode(gateMode === "login" ? "register" : "login")
);

$("#gate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const username = (form.get("username") || "").trim();
  const password = form.get("password") || "";
  try {
    const result = gateMode === "register"
      ? await api.register({
          username,
          password,
          invite_code: (form.get("invite_code") || "").trim(),
        })
      : await api.login(username, password);
    state.user = result.user;
    await enterApp();
  } catch (problem) {
    $("#gate-error").textContent = problem.message;
  }
});

// ------------------------------------------------------------------- app --
async function enterApp() {
  $("#gate").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#whoami").textContent = state.user.display_name || state.user.username;
  await Promise.all([refreshTasks(), refreshView(), refreshTimer()]);
  $("#compose-input").focus();
}

$("#sign-out").addEventListener("click", async () => {
  await api.logout();
  state.user = null;
  stopTimerTick();
  setGateMode("login");
  showGate();
});

// -------------------------------------------------------------- compose --
$("#compose-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#compose-input");
  const text = input.value.trim();
  if (!text) return;
  try {
    const result = await api.addTask(text);
    input.value = "";
    setStatus(result.message, `read as: ${result.understood.join(", ")}`);
    await Promise.all([refreshTasks(), refreshView({ keepStatus: true })]);
  } catch (problem) {
    setStatus(problem.message);
  }
});

// ----------------------------------------------------------- task list ----
async function refreshTasks() {
  const { tasks } = await api.tasks();
  state.tasks = tasks;
  drawTasks();
}

function drawTasks() {
  const list = $("#task-list");
  clear(list);

  const live = state.tasks.filter((task) => !task.is_done);
  if (!live.length) {
    list.append(el("p", { class: "empty", text: "Nothing on the list. Type something above." }));
    return;
  }

  const today = isoToday();
  for (const task of live) {
    const classes = ["task"];
    if (task.due_date && task.due_date < today) classes.push("is-overdue");
    if (task.times_deferred >= 3) classes.push("is-stalled");

    const when = task.start_time
      ? `${task.due_date || task.scheduled_date || today} ${task.start_time}`
      : task.scheduled_date || task.due_date || "no date";

    list.append(
      el(
        "div",
        {
          class: classes.join(" "),
          "data-id": task.id,
          "aria-selected": String(state.selectedTaskId === task.id),
          onclick: () => {
            state.selectedTaskId = task.id;
            drawTasks();
          },
        },
        [
          el("span", { class: "task-title", text: task.title }),
          el("span", { class: "task-est num", text: humanMinutes(task.estimated_minutes) }),
          el("span", {
            class: "task-meta",
            text: [
              when,
              `#${task.category}`,
              ["", "must", "should", "maybe"][task.priority],
              task.times_deferred ? `pushed ${task.times_deferred}×` : "",
            ].filter(Boolean).join(" · "),
          }),
        ]
      )
    );
  }
}

$("#task-actions").addEventListener("click", async (event) => {
  const action = event.target.dataset.act;
  if (!action) return;

  if (action === "undo") return run(() => api.undo());

  const id = state.selectedTaskId;
  if (!id) return setStatus("Select a task first.");
  const task = state.tasks.find((candidate) => candidate.id === id);

  if (action === "done") {
    const typed = window.prompt(
      `"${task.title}"\n\nEstimated at ${task.estimated_minutes} minutes.\n` +
      "Actual minutes (this is the only calibration input):",
      task.estimated_minutes
    );
    if (typed === null) return;
    return run(() => api.completeTask(id, Number(typed)));
  }
  if (action === "defer") return run(() => api.deferTask(id, 1));
  if (action === "delete") {
    if (!window.confirm(`Delete "${task.title}"?`)) return;
    return run(() => api.deleteTask(id));
  }
  if (action === "edit") {
    const title = window.prompt("Title", task.title);
    if (title === null) return;
    const estimate = window.prompt("Estimated minutes", task.estimated_minutes);
    if (estimate === null) return;
    return run(() => api.editTask(id, { title, estimated_minutes: Number(estimate) }));
  }
  if (action === "timer") {
    return run(async () => {
      const result = await api.startTimer(id);
      await refreshTimer();
      return result;
    });
  }
});

async function run(work) {
  try {
    const result = await work();
    if (result?.message) setStatus(result.message);
    await Promise.all([refreshTasks(), refreshView({ keepStatus: Boolean(result?.message) })]);
  } catch (problem) {
    setStatus(problem.message);
  }
}

// ------------------------------------------------------------------ views --
$$(".view-tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    state.view = tab.dataset.view;
    $$(".view-tab").forEach((other) =>
      other.setAttribute("aria-selected", String(other === tab))
    );
    refreshView();
  })
);

const navHandlers = {
  previous: () => moveDate(-1),
  next: () => moveDate(1),
  today: () => {
    state.date = isoToday();
    refreshView();
  },
};

function moveDate(direction) {
  if (state.view === "day") {
    state.date = shiftIso(state.date, direction);
  } else if (state.view === "week") {
    state.date = shiftIso(state.date, 7 * direction);
  } else {
    const [year, month] = state.date.split("-").map(Number);
    const moved = new Date(year, month - 1 + direction, 1);
    state.date =
      `${moved.getFullYear()}-${String(moved.getMonth() + 1).padStart(2, "0")}-01`;
  }
  refreshView();
}

async function refreshView({ keepStatus = false } = {}) {
  const container = $("#calendar");
  try {
    if (state.view === "day") {
      const day = await api.day(state.date);
      state.day = day;
      renderDay(container, day, navHandlers);
      if (!keepStatus) setStatus(day.status, day.calibration.describes);
    } else if (state.view === "week") {
      const week = await api.week(mondayOf(state.date));
      renderWeek(container, week, navHandlers);
      if (!keepStatus) {
        setStatus(`${humanMinutes(week.week_minutes)} planned across the week.`);
      }
    } else {
      const month = await api.month(state.date.slice(0, 7));
      renderMonth(container, month, navHandlers);
      if (!keepStatus) setStatus("Each bar is one day against capacity.");
    }
  } catch (problem) {
    if (problem.message.includes("Sign in")) return showGate();
    setStatus(problem.message);
  }
}

wireCalendarInteractions($("#calendar"), {
  onCommit: async (sessionId, payload) => {
    const result = await api.moveSession(sessionId, payload);
    state.day = result.day;
    if (state.view === "day") {
      renderDay($("#calendar"), result.day, navHandlers);
      setStatus(result.day.status, result.day.calibration.describes);
    } else {
      await refreshView();
    }
  },
  onError: (message) => setStatus(message),
  onCreate: async ({ day, startMinute }) => {
    const text = window.prompt(
      `New task at ${String(Math.floor(startMinute / 60)).padStart(2, "0")}:` +
      `${String(startMinute % 60).padStart(2, "0")}`,
      ""
    );
    if (!text) return;
    try {
      await api.createSession({ text, day, start_minute: startMinute, minutes: 30 });
      await Promise.all([refreshTasks(), refreshView()]);
    } catch (problem) {
      setStatus(problem.message);
    }
  },
});

// ------------------------------------------------------------------ timer --
async function refreshTimer() {
  const { running } = await api.timer();
  const bar = $("#timer");
  if (!running) {
    bar.classList.add("is-idle");
    stopTimerTick();
    return;
  }
  bar.classList.remove("is-idle");
  $("#timer-label").textContent = running.title;
  let elapsed = running.elapsed_seconds;
  const paint = () => {
    const minutes = Math.floor(elapsed / 60);
    $("#timer-elapsed").textContent =
      `${minutes}:${String(elapsed % 60).padStart(2, "0")}`;
    elapsed += 1;
  };
  paint();
  stopTimerTick();
  state.timerTick = window.setInterval(paint, 1000);
}

function stopTimerTick() {
  if (state.timerTick) window.clearInterval(state.timerTick);
  state.timerTick = null;
}

$("#timer-stop").addEventListener("click", async () => {
  try {
    const result = await api.stopTimer(false);
    setStatus(result.message);
    await Promise.all([refreshTimer(), refreshTasks(), refreshView({ keepStatus: true })]);
  } catch (problem) {
    setStatus(problem.message);
  }
});

// ----------------------------------------------------------------- status --
function setStatus(line, note = "") {
  const element = $("#status-line");
  element.textContent = line || "";
  element.classList.toggle(
    "is-over",
    /Overbooked|did not fit|Double booked|Slightly over/.test(line || "")
  );
  $("#status-note").textContent = note;
}

// ------------------------------------------------------------------ start --
(async function start() {
  try {
    const { user } = await api.me();
    state.user = user;
    await enterApp();
  } catch {
    setGateMode("login");
    showGate();
  }
})();
