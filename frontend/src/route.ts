/**
 * The two experiences are different routes, not a toggle inside one screen.
 *
 *   /companion  the person's full-screen view — no login, no caregiver data
 *   /caregiver  the caregiver dashboard — login required
 *   /           the default entry point, i.e. the caregiver login
 *   /?demo=split  hidden judge/demo layout (avatar + caregiver dashboard)
 *
 * Anything unknown falls back to the caregiver entry, never to the companion:
 * a typo must not silently open a view that looks like it has no account.
 */
export type AppRoute = "companion" | "caregiver" | "demo-split";

export function parseRoute(pathname: string, search: string): AppRoute {
  const path = pathname.replace(/\/+$/, "") || "/";

  if (path === "/companion") return "companion";
  if (path === "/caregiver") return "caregiver";

  if (path === "/") {
    const demo = new URLSearchParams(search).get("demo");
    return demo === "split" ? "demo-split" : "caregiver";
  }

  return "caregiver";
}
