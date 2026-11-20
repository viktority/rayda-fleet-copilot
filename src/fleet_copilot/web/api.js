// A Fleet token is scoped to a company/role. The OpenAI credential never reaches the browser.
let token = "";
let generation = 0;
const pending = new Set();

export const epoch = () => generation;
export function setToken(value) {
  generation += 1;
  for (const controller of pending) controller.abort();
  pending.clear();
  token = value;
  try {
    if (token) sessionStorage.setItem("fleet-access", token);
    else sessionStorage.removeItem("fleet-access");
  } catch {
    /* Private browsing can disable storage; the in-memory session still works. */
  }
}

export function initialToken() {
  const access = new URLSearchParams(location.hash.slice(1)).get("access");
  if (access)
    history.replaceState(null, "", location.pathname + location.search);
  try {
    return access || sessionStorage.getItem("fleet-access") || "";
  } catch {
    return access || "";
  }
}

export async function request(path, options = {}) {
  if (!token) throw new Error("Connect to your workspace first.");
  const started = generation;
  const controller = new AbortController();
  pending.add(controller);
  const timer = setTimeout(() => controller.abort(), 240000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      signal: controller.signal,
      cache: "no-store",
    });
    if (started !== generation) throw new Error("Connection changed.");
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) {
        window.dispatchEvent(new Event("fleet-unauthorized"));
        throw new Error(
          "Your connection has expired. Reopen the local launcher or reconnect.",
        );
      }
      if (response.status === 429)
        throw new Error(
          "Too many requests. Wait a minute before trying again.",
        );
      if (response.status === 504)
        throw new Error(
          "The service took too long. Check Action reviews before retrying a proposal request.",
        );
      throw new Error(
        data?.message ||
          (typeof data?.detail === "string"
            ? data.detail
            : "The request could not be completed. Check your inputs and try again."),
      );
    }
    if (data === null)
      throw new Error(
        "The service returned an unreadable response. Try again.",
      );
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "The request stopped or timed out. Check Action reviews before retrying a proposal request.",
      );
    if (error instanceof TypeError)
      throw new Error(
        "Cannot reach the Fleet service. Check that it is running and try again.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
    pending.delete(controller);
  }
}

export const post = (path, body) =>
  request(path, { method: "POST", body: JSON.stringify(body) });
