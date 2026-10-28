const rawApiBase = (import.meta as any).env?.VITE_API_BASE_URL as string | undefined;

/** Base URL for the Sahaara API. Defaults to the same-origin /api proxy. */
export const API_BASE = (rawApiBase?.trim() || "/api").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${suffix}`;
}

/** fetch wrapper: one API base, cookies included. */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), {
    credentials: "include",
    ...init,
  });
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

  return streamWsUrl(path);
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
