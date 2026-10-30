const rawApiBase = (import.meta as any).env?.VITE_API_BASE_URL as string | undefined;

/** Base URL for the Sahaara API. Defaults to the same-origin /api proxy. */
export const API_BASE = (rawApiBase?.trim() || "/api").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${suffix}`;
}

/**
 * Fetch without throwing on a non-2xx answer.
 *
 * Use this where a 4xx is a normal answer rather than a failure — most
 * importantly `GET /auth/me`, where **401 means "nobody is signed in yet"**.
 * With `apiFetch` that 401 throws, and a caller that only catches generically
 * cannot tell "not logged in" apart from "the server is broken".
 */
export async function apiFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), {
    credentials: "include",
    ...init,
  });
}

/** fetch wrapper: one API base, cookies included. Throws on a non-2xx answer. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await apiFetchRaw(path, init);
  if (!res.ok) {
    const errorText = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${errorText || res.statusText}`);
  }
  return res;
}

/**
 * EventSource URL.
 *
 * The SSE endpoint authenticates from the session cookie (condition 1 of M5
 * Step 1), so no token goes in the URL any more — query tokens leak into
 * access logs, browser history and `Referer` headers for no benefit.
 */
export function eventSourceUrl(path: string): string {
  return apiUrl(path);
}

/** Open the SSE stream with the session cookie attached. */
export function openEventSource(path: string): EventSource {
  return new EventSource(eventSourceUrl(path), { withCredentials: true });
}

/** Open a WebSocket to the proxied stream endpoint.
 *
 * Same-origin, so the browser sends the session cookie automatically — no
 * token in the URL. Goes through the Vite `/api` proxy (`ws: true`), which
 * is what makes live updates work over Cloudflare Quick Tunnel (SSE bodies
 * are buffered there but WebSocket frames pass through).
 */
export function openWebSocket(path: string): WebSocket {
  return new WebSocket(apiUrl(path));
}

/** WebSocket URL derived from the current page (https -> wss). */
export function wsUrl(path: string): string {
  const explicit = (import.meta as any).env?.VITE_VOICE_WS_URL as string | undefined;
  if (explicit?.trim()) return explicit.trim();

  // In development, always go through the /api proxy so the WebSocket
  // is proxied to the backend (Vite proxy handles ws: true).
  // The proxy rewrites /api/ws/voice -> /ws/voice on the backend.
  // The HttpOnly cookie is sent automatically with the WebSocket upgrade.
  return apiUrl("/ws" + path);
}

/**
 * WebSocket URL for a same-origin stream endpoint, ignoring the voice-only
 * override. Used by the companion channel: `VITE_VOICE_WS_URL` points at the
 * voice socket and must never redirect a different stream.
 */
export function streamWsUrl(path: string): string {
  const url = new URL(apiUrl(path), window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
