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

/** EventSource URL; the SSE endpoint still accepts its query token. */
export function eventSourceUrl(path: string, token?: string | null): string {
  const url = apiUrl(path);
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

/** WebSocket URL derived from the current page (https -> wss). */
export function wsUrl(path: string): string {
  const explicit = (import.meta as any).env?.VITE_VOICE_WS_URL as string | undefined;
  if (explicit?.trim()) return explicit.trim();

  const url = new URL(apiUrl(path), window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
