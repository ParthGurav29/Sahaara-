/**
 * deepLink.ts
 *
 * The smallest deep link the app supports (M5 Step 7): `/?alert=<id>`.
 *
 * The app has no router, so this is deliberately a query parameter read once on
 * load. The service worker builds it from the opaque `alert_id` in a push; the
 * SPA then loads that alert's context from the existing
 * `GET /alerts/{id}/context` endpoint. Ids are validated on both sides, and an
 * unknown or expired id must produce the calm message below — never a blank
 * screen (cf. the Oct 20 blank-screen bug).
 *
 * Pure module: `node --test` imports it directly.
 */

export const ALERT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export const ALERT_NOT_FOUND_MESSAGE =
  "That alert is no longer available. The dashboard below shows the latest state.";

/** The alert id from a query string, or null when absent/invalid. */
export function parseAlertId(search: string): string | null {
  if (!search) return null;
  let value: string | null = null;
  try {
    value = new URLSearchParams(search).get("alert");
  } catch {
    return null;
  }
  if (!value) return null;
  const trimmed = value.trim();
  return ALERT_ID_RE.test(trimmed) ? trimmed : null;
}

/** The same URL with `alert` removed, so a refresh does not re-trigger it. */
export function withoutAlertParam(href: string): string {
  try {
    const url = new URL(href);
    url.searchParams.delete("alert");
    return url.pathname + url.search + url.hash;
  } catch {
    return href;
  }
}
