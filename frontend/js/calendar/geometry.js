// geometry.js
// The whole calendar is one conversion: a minute of the day becomes a pixel
// offset. Every position on screen comes from here.

export function pixelsPerMinute() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--px-per-min");
  return parseFloat(raw) || 1.1;
}

export function snapMinutes() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--snap-minutes");
  return parseInt(raw, 10) || 5;
}

export function makeScale(viewStartMinute, pxPerMin) {
  return {
    viewStartMinute,
    pxPerMin,
    y: (minute) => (minute - viewStartMinute) * pxPerMin,
    minuteAt: (offsetY) => viewStartMinute + offsetY / pxPerMin,
    height: (minutes) => Math.max(14, minutes * pxPerMin),
  };
}

export function snapTo(minute, step) {
  return Math.round(minute / step) * step;
}

// Blocks that overlap in time share the width rather than hiding each other.
// A pinned session can legitimately land on top of a fixed commitment.
export function assignLanes(blocks) {
  const ordered = [...blocks].sort(
    (a, b) => a.start_minute - b.start_minute || a.end_minute - b.end_minute
  );
  const placements = new Map();
  let group = [];
  let groupEnd = -1;

  const settleGroup = () => {
    if (!group.length) return;
    const lanes = [];
    for (const block of group) {
      let lane = lanes.findIndex((endMinute) => endMinute <= block.start_minute);
      if (lane === -1) {
        lanes.push(block.end_minute);
        lane = lanes.length - 1;
      } else {
        lanes[lane] = block.end_minute;
      }
      placements.set(block, { lane, lanes: 0 });
    }
    for (const block of group) placements.get(block).lanes = lanes.length;
    group = [];
    groupEnd = -1;
  };

  for (const block of ordered) {
    if (group.length && block.start_minute >= groupEnd) settleGroup();
    group.push(block);
    groupEnd = Math.max(groupEnd, block.end_minute);
  }
  settleGroup();
  return placements;
}
