// dom.js — element building and the handful of formatters used everywhere.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value == null || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.firstChild.remove();
}

// -- formatters ------------------------------------------------------------
export function clockOf(minutes) {
  const whole = Math.max(0, Math.round(minutes));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

export function humanMinutes(minutes) {
  const whole = Math.round(minutes);
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  if (hours && rest) return `${hours}h ${rest}m`;
  if (hours) return `${hours}h`;
  return `${rest}m`;
}

export function isoToday() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

export function shiftIso(iso, days) {
  const parsed = new Date(`${iso}T00:00:00`);
  parsed.setDate(parsed.getDate() + days);
  return isoOf(parsed);
}

export function isoOf(dateObject) {
  return [
    dateObject.getFullYear(),
    String(dateObject.getMonth() + 1).padStart(2, "0"),
    String(dateObject.getDate()).padStart(2, "0"),
  ].join("-");
}

export function mondayOf(iso) {
  const parsed = new Date(`${iso}T00:00:00`);
  const weekday = (parsed.getDay() + 6) % 7;   // Monday is 0
  return shiftIso(iso, -weekday);
}

export function minutesNow() {
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}
