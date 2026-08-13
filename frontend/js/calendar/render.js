// render.js
// Draws a day's column from server truth. Containers are rebuilt whole rather
// than patched, which is the rule that keeps stale-DOM bugs out. The one
// exception is a drag in flight, handled in drag.js.

import { clear, clockOf, el, humanMinutes, minutesNow } from "../dom.js";
import { assignLanes, makeScale } from "./geometry.js";

export function columnHeightFor(day, scale) {
  const lastOverflow = day.overflow_blocks?.length
    ? day.overflow_blocks[day.overflow_blocks.length - 1].end_minute
    : day.view_end_minute;
  return scale.y(Math.max(day.view_end_minute, lastOverflow)) + 24;
}

export function renderHourGutter(container, day, scale, height) {
  clear(container);
  container.style.height = `${height}px`;
  const firstHour = Math.ceil(day.view_start_minute / 60) * 60;
  const lastMinute = scale.viewStartMinute + height / scale.pxPerMin;
  for (let minute = firstHour; minute <= lastMinute; minute += 60) {
    container.append(
      el("div", {
        class: "hour-label num",
        style: `top:${scale.y(minute)}px`,
        text: clockOf(minute),
      })
    );
  }
}

function blockNode(block, scale, placement, extraClass = "") {
  const lanes = placement?.lanes || 1;
  const lane = placement?.lane || 0;
  const width = 100 / lanes;

  const classes = ["block"];
  if (block.submerged) classes.push("is-submerged");
  if (block.is_overflow) classes.push("is-overflow");
  if (extraClass) classes.push(extraClass);

  const node = el(
    "div",
    {
      class: classes.join(" "),
      style:
        `top:${scale.y(block.start_minute)}px;` +
        `height:${scale.height(block.minutes)}px;` +
        `left:calc(${lane * width}% + 2px);width:calc(${width}% - 6px);`,
      "data-energy": block.energy_level || "normal",
      "data-kind": block.kind,
      "data-origin": block.origin || "auto",
      "data-session": block.session_id ?? "",
      "data-task": block.task_id ?? "",
      "data-minutes": block.minutes,
      "data-start": block.start_minute,
    },
    [
      el("div", { class: "block-time num", text: block.is_overflow
        ? `${humanMinutes(block.minutes)} unplaced`
        : `${clockOf(block.start_minute)}–${clockOf(block.end_minute)}` }),
      el("div", { class: "block-label", text: block.label }),
      block.of > 1
        ? el("div", { class: "block-seq num", text: `${block.sequence} of ${block.of}` })
        : null,
    ]
  );

  const draggable = block.kind === "task" && !block.is_overflow && block.session_id != null;
  if (draggable) node.append(el("div", { class: "resize-handle" }));
  else if (block.kind !== "task" || block.is_overflow) node.style.cursor = "default";

  return node;
}

export function renderColumn(column, day, scale, height, { showNow = true } = {}) {
  clear(column);
  column.style.height = `${height}px`;

  // Hour rules.
  const firstHour = Math.ceil(day.view_start_minute / 60) * 60;
  const lastMinute = scale.viewStartMinute + height / scale.pxPerMin;
  for (let minute = firstHour; minute <= lastMinute; minute += 60) {
    column.append(el("div", { class: "hour-rule", style: `top:${scale.y(minute)}px` }));
  }

  // Scheduled work.
  const placements = assignLanes(day.blocks);
  for (const block of day.blocks) {
    column.append(blockNode(block, scale, placements.get(block)));
  }

  // The end of the working day, after which anything drawn is overflow.
  column.append(
    el("div", { class: "end-of-day", style: `top:${scale.y(day.view_end_minute)}px` })
  );

  // Minutes that did not fit, at the same scale so their height is honest.
  for (const block of day.overflow_blocks || []) {
    column.append(blockNode({ ...block, submerged: false }, scale, null));
  }
  if (day.overflow_minutes > 0) {
    column.append(
      el("div", {
        class: "overflow-caption",
        style: `top:${scale.y(day.view_end_minute) + 4}px`,
        text: `${humanMinutes(day.overflow_minutes)} does not fit`,
      })
    );
  }

  // The waterline: where the day's work reaches measured capacity.
  if (day.waterline_minute != null) {
    column.append(
      el("div", {
        class: "waterline",
        style: `top:${scale.y(day.waterline_minute)}px`,
        "data-label": humanMinutes(day.capacity_minutes),
      })
    );
  }

  if (showNow && day.is_today) {
    const minute = minutesNow();
    if (minute >= day.view_start_minute && minute <= lastMinute) {
      column.append(el("div", { class: "now-line", style: `top:${scale.y(minute)}px` }));
    }
  }
}

export function scaleFor(day, pxPerMin) {
  return makeScale(day.view_start_minute, pxPerMin);
}
