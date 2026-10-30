/**
 * progress.ts
 *
 * Drives the #progress-bar and #splash that index.html inlines for a non-blank
 * first paint. Pure DOM, no React state: it works before React hydrates and
 * never triggers a re-render. The bar never reaches 100% on its own — only
 * progressDone() finishes it — so it can't claim to be done while a lazy chunk
 * is still loading.
 */

const bar = (): HTMLElement | null => document.getElementById("progress-bar");
const splash = (): HTMLElement | null => document.getElementById("splash");

let raf = 0;
let resetTimer: number | undefined;
let value = 0;

const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function set(pct: number): void {
  value = pct;
  const el = bar();
  if (!el) return;
  el.style.transform = `scaleX(${pct / 100})`;
  el.setAttribute("aria-valuenow", String(Math.round(pct)));
}

/**
 * Start the "still loading" ramp. Eases toward 90% and stops there; only
 * progressDone() reaches 100%. Under prefers-reduced-motion it settles on a
 * static half-way mark instead of animating.
 */
export function progressStart(): void {
  if (typeof window === "undefined") return;
  cancelAnimationFrame(raf);
  bar()?.classList.remove("done");

  if (prefersReducedMotion()) {
    set(60);
    return;
  }

  value = 8;
  const tick = (): void => {
    value = Math.min(value + (90 - value) * 0.12, 90);
    set(value);
    if (!bar()?.classList.contains("done")) raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
}

/** Complete the bar: 100% → fade → reset to 0 so the next route starts clean. */
export function progressDone(): void {
  if (typeof window === "undefined") return;
  cancelAnimationFrame(raf);
  const el = bar();
  set(100);
  el?.classList.add("done");
  if (resetTimer) window.clearTimeout(resetTimer);
  resetTimer = window.setTimeout(() => {
    el?.classList.remove("done");
    set(0);
  }, 300);
}

/** Fade the splash overlay out once React has its first real content on screen. */
export function hideSplash(): void {
  splash()?.classList.add("hidden");
}