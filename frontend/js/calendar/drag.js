// drag.js
// Move and resize with Pointer Events, so mouse, trackpad, touch and pen all
// take one path. setPointerCapture keeps the drag alive when the cursor
// leaves the block.
//
// Nothing is fetched while the pointer is down. One element's inline style
// changes, and the move is committed on release; the server then returns the
// whole replanned day, which replaces the container.

import { clockOf } from "../dom.js";
import { pixelsPerMinute, snapMinutes, snapTo } from "./geometry.js";

const MINIMUM_MINUTES = 5;

export function attachDrag(root, { onCommit, onError, columnsFor }) {
  root.addEventListener("pointerdown", (event) => {
    const block = event.target.closest(".block");
    if (!block) return;
    if (block.dataset.kind !== "task") return;
    if (block.classList.contains("is-overflow")) return;
    const sessionId = block.dataset.session;
    if (!sessionId) return;                       // only pinned blocks move

    const mode = event.target.classList.contains("resize-handle") ? "resize" : "move";
    const pxPerMin = pixelsPerMinute();
    const step = snapMinutes();

    // Read layout once. Measuring per pointermove is the usual source of jank.
    const columns = (columnsFor ? columnsFor() : []).map((column) => ({
      element: column,
      day: column.dataset.day,
      rect: column.getBoundingClientRect(),
    }));

    const start = {
      pointerY: event.clientY,
      startMinute: Number(block.dataset.start),
      minutes: Number(block.dataset.minutes),
      day: block.closest("[data-day]")?.dataset.day,
      column: block.parentElement,
    };
    const label = block.querySelector(".block-time");

    let next = { ...start, changed: false };

    block.setPointerCapture(event.pointerId);
    block.classList.add("is-dragging");
    document.body.classList.add("is-dragging");

    const onMove = (moveEvent) => {
      const deltaMinutes = snapTo((moveEvent.clientY - start.pointerY) / pxPerMin, step);

      if (mode === "resize") {
        next.minutes = Math.max(MINIMUM_MINUTES, start.minutes + deltaMinutes);
        block.style.height = `${Math.max(14, next.minutes * pxPerMin)}px`;
        label.textContent =
          `${clockOf(next.startMinute)}–${clockOf(next.startMinute + next.minutes)}`;
      } else {
        next.startMinute = Math.max(0, start.startMinute + deltaMinutes);
        block.style.transform = `translateY(${(next.startMinute - start.startMinute) * pxPerMin}px)`;
        label.textContent =
          `${clockOf(next.startMinute)}–${clockOf(next.startMinute + next.minutes)}`;

        // Week view: moving across columns changes the day.
        const overColumn = columns.find(
          (candidate) =>
            moveEvent.clientX >= candidate.rect.left &&
            moveEvent.clientX <= candidate.rect.right
        );
        if (overColumn && overColumn.day !== next.day) {
          next.day = overColumn.day;
          overColumn.element.append(block);
          block.style.left = "2px";
          block.style.width = "calc(100% - 6px)";
        }
      }
      next.changed = true;
    };

    const finish = async () => {
      block.removeEventListener("pointermove", onMove);
      block.removeEventListener("pointerup", finish);
      block.removeEventListener("pointercancel", cancel);
      block.classList.remove("is-dragging");
      document.body.classList.remove("is-dragging");

      if (!next.changed) return;
      if (next.startMinute === start.startMinute
          && next.minutes === start.minutes
          && next.day === start.day) return;

      try {
        await onCommit(Number(sessionId), {
          day: next.day,
          start_minute: next.startMinute,
          minutes: next.minutes,
        });
      } catch (problem) {
        revert();
        onError?.(problem.message);
      }
    };

    const revert = () => {
      block.style.transform = "";
      block.style.height = `${Math.max(14, start.minutes * pxPerMin)}px`;
      label.textContent =
        `${clockOf(start.startMinute)}–${clockOf(start.startMinute + start.minutes)}`;
      if (block.parentElement !== start.column) start.column.append(block);
    };

    const cancel = () => {
      block.removeEventListener("pointermove", onMove);
      block.removeEventListener("pointerup", finish);
      block.removeEventListener("pointercancel", cancel);
      block.classList.remove("is-dragging");
      document.body.classList.remove("is-dragging");
      revert();
    };

    block.addEventListener("pointermove", onMove);
    block.addEventListener("pointerup", finish);
    block.addEventListener("pointercancel", cancel);
  });
}

// Clicking empty space in a column offers to put something there.
export function attachSlotCreate(root, { onCreate, viewStartFor }) {
  root.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".block")) return;
    const column = event.target.closest(".column");
    if (!column) return;

    const pxPerMin = pixelsPerMinute();
    const step = snapMinutes();
    const rect = column.getBoundingClientRect();
    const viewStart = viewStartFor(column);
    const minute = snapTo(viewStart + (event.clientY - rect.top) / pxPerMin, step);

    onCreate({ day: column.dataset.day, startMinute: Math.max(0, minute) });
  });
}
