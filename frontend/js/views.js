// views.js — day, week and month. Each answers a different question:
// day, what am I doing now; week, where does this fit; month, when am I free.

import { clear, el, humanMinutes, isoToday, shiftIso } from "./dom.js";
import { attachDrag, attachSlotCreate } from "./calendar/drag.js";
import { pixelsPerMinute } from "./calendar/geometry.js";
import { columnHeightFor, renderColumn, renderHourGutter, scaleFor } from "./calendar/render.js";

function navBar(title, dateText, handlers) {
  return el("div", { class: "daybar" }, [
    el("h2", { text: title }),
    el("span", { class: "date num", text: dateText }),
    el("div", { class: "nav" }, [
      el("button", { type: "button", text: "‹", onclick: handlers.previous, title: "Previous" }),
      el("button", { type: "button", text: "Today", onclick: handlers.today }),
      el("button", { type: "button", text: "›", onclick: handlers.next, title: "Next" }),
    ]),
  ]);
}

// ---------------------------------------------------------------- day -----
export function renderDay(container, day, handlers) {
  clear(container);
  container.append(navBar(day.friendly, day.date, handlers));

  const grid = el("div", { class: "grid", "data-view-start": day.view_start_minute });
  const hours = el("div", { class: "hours" });
  const columns = el("div", { class: "columns", style: "grid-template-columns:1fr" });
  const column = el("div", { class: "column", "data-day": day.date });

  grid.append(hours, columns);
  columns.append(column);
  container.append(grid);

  const scale = scaleFor(day, pixelsPerMinute());
  const height = columnHeightFor(day, scale);
  renderHourGutter(hours, day, scale, height);
  renderColumn(column, day, scale, height);

  return { scale, columns: [column] };
}

// --------------------------------------------------------------- week -----
export function renderWeek(container, week, handlers) {
  clear(container);
  const first = week.days[0];
  const last = week.days[week.days.length - 1];
  container.append(navBar("Week", `${first.date} → ${last.date}`, handlers));

  const grid = el("div", { class: "grid" });
  const hours = el("div", { class: "hours" });
  const columns = el("div", {
    class: "columns",
    style: `grid-template-columns:repeat(${week.days.length}, 1fr)`,
  });
  grid.append(hours, columns);
  container.append(grid);

  // One scale for the whole week, so the columns line up.
  const viewStart = Math.min(...week.days.map((d) =>
    Math.min(week.day_start_minute, ...d.blocks.map((b) => b.start_minute))));
  const viewEnd = Math.max(...week.days.map((d) =>
    Math.max(week.day_end_minute, ...d.blocks.map((b) => b.end_minute))));
  const shared = {
    view_start_minute: Math.floor(viewStart / 60) * 60,
    view_end_minute: viewEnd,
    overflow_blocks: [],
  };
  grid.dataset.viewStart = shared.view_start_minute;

  const scale = scaleFor(shared, pixelsPerMinute());
  const height = scale.y(viewEnd) + 24;
  renderHourGutter(hours, shared, scale, height);

  const built = [];
  for (const day of week.days) {
    const wrapper = el("div", { class: "column", "data-day": day.date });
    const head = el("div", {
      class: `column-head${day.is_today ? " is-today" : ""}`,
      text: `${day.friendly} · ${humanMinutes(day.intended_minutes)}`,
    });
    columns.append(head);
    columns.append(wrapper);
    renderColumn(
      wrapper,
      { ...day, ...shared, capacity_minutes: week.capacity_minutes, overflow_minutes: 0 },
      scale,
      height,
    );
    built.push(wrapper);
  }

  // Heads and columns interleave, so re-lay them into two rows.
  columns.style.gridTemplateRows = "auto 1fr";
  columns.style.gridAutoFlow = "column";
  return { scale, columns: built };
}

// -------------------------------------------------------------- month -----
export function renderMonth(container, month, handlers) {
  clear(container);
  container.append(navBar("Month", month.month, handlers));

  const grid = el("div", { class: "month" });
  const firstWeekday = (new Date(`${month.days[0].date}T00:00:00`).getDay() + 6) % 7;
  for (let blank = 0; blank < firstWeekday; blank += 1) {
    grid.append(el("div", { class: "month-cell" }));
  }

  for (const day of month.days) {
    // Load is drawn as fill height, since no hue is free to encode a ramp.
    const withinCapacity = Math.min(day.load_ratio, 1) * 100;
    const beyond = Math.min(Math.max(day.load_ratio - 1, 0), 1) * 100;
    grid.append(
      el("div", { class: `month-cell${day.is_today ? " is-today" : ""}` }, [
        el("span", { class: "month-day", text: day.date.slice(-2) }),
        el("div", { class: "month-bar" }, [
          beyond ? el("div", { class: "month-over", style: `height:${beyond}%` }) : null,
          el("div", { class: "month-fill", style: `height:${withinCapacity}%` }),
        ]),
        el("span", {
          class: "month-load",
          text: day.intended_minutes ? humanMinutes(day.intended_minutes) : "",
        }),
      ])
    );
  }

  container.append(grid);
  return { columns: [] };
}

// ------------------------------------------------------------- wiring -----
export function wireCalendarInteractions(container, { onCommit, onError, onCreate }) {
  attachDrag(container, {
    onCommit,
    onError,
    columnsFor: () => [...container.querySelectorAll(".column[data-day]")],
  });
  attachSlotCreate(container, {
    onCreate,
    // The grid records where its axis begins, so the click position converts
    // back to a clock minute without re-deriving it from the hour labels.
    viewStartFor: (column) =>
      Number(column.closest(".grid")?.dataset.viewStart ?? 0),
  });
}

export const dateHelpers = { isoToday, shiftIso };
