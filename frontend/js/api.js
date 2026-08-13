// api.js
// One fetch wrapper. Every request sends JSON, because the server refuses
// mutations that do not, which is what stands in for a CSRF token.
// The server's {"error": "..."} sentence becomes the thrown message, so the
// interface can print it without a translation layer.

async function request(method, path, body) {
  const options = {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
  };
  if (method !== "GET") options.body = JSON.stringify(body ?? {});

  const response = await fetch(path, options);

  let data = null;
  try {
    data = await response.json();
  } catch {
    throw new Error("The server sent something unreadable.");
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  patch: (path, body) => request("PATCH", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path, body) => request("DELETE", path, body),

  me: () => request("GET", "/api/me"),
  login: (username, password) => request("POST", "/api/login", { username, password }),
  register: (payload) => request("POST", "/api/register", payload),
  logout: () => request("POST", "/api/logout", {}),

  tasks: () => request("GET", "/api/tasks"),
  addTask: (text) => request("POST", "/api/tasks", { text }),
  editTask: (id, changes) => request("PATCH", `/api/tasks/${id}`, changes),
  completeTask: (id, actual) =>
    request("POST", `/api/tasks/${id}/complete`, actual == null ? {} : { actual_minutes: actual }),
  deferTask: (id, days = 1) => request("POST", `/api/tasks/${id}/defer`, { days }),
  deleteTask: (id) => request("DELETE", `/api/tasks/${id}`, {}),
  undo: () => request("POST", "/api/undo", {}),

  day: (date) => request("GET", `/api/plan/day?date=${date}`),
  week: (start) => request("GET", `/api/plan/week?start=${start}`),
  month: (month) => request("GET", `/api/plan/month?month=${month}`),

  createSession: (payload) => request("POST", "/api/sessions", payload),
  moveSession: (id, payload) => request("PATCH", `/api/sessions/${id}`, payload),
  unpinSession: (id) => request("DELETE", `/api/sessions/${id}`, {}),

  timer: () => request("GET", "/api/timer"),
  startTimer: (taskId) => request("POST", "/api/timer/start", { task_id: taskId }),
  stopTimer: (complete = false) => request("POST", "/api/timer/stop", { complete }),
};
