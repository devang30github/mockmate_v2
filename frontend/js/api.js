export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function describe(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d.msg || "Invalid input").replace(/^Value error, /, "")).join(". ");
  }
  return `Request failed (${status})`;
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = null;
  if (res.status !== 204) {
    try { data = await res.json(); } catch { /* non-JSON body */ }
  }
  if (!res.ok) throw new ApiError(res.status, describe(data, res.status));
  return data;
}