import { useEffect, useState } from "react";

import { parseRoute, type AppRoute } from "./route";

/**
 * Read the route from the real URL and keep it in sync with back/forward.
 *
 * No router dependency: the app has two destinations and one hidden demo
 * query, so a full routing library would be more surface than value.
 */
export function useRoute(): AppRoute {
  const [route, setRoute] = useState<AppRoute>(() =>
    parseRoute(window.location.pathname, window.location.search),
  );

  useEffect(() => {
    const onPopState = () => {
      setRoute(parseRoute(window.location.pathname, window.location.search));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return route;
}

/** Programmatic navigation — pushes to history and updates the route state. */
export function navigateTo(path: string): void {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
