/**
 * Pure decision logic for the M5 PWA shell.
 *
 * Kept free of browser APIs so it can be unit-tested in Node (see
 * `frontend/tests/pwaGate.test.ts`). `pwa.ts` does the DOM/service-worker work.
 */

export type PwaAction = "enable" | "disable" | "keep";

export interface PwaSignals {
  /** Build-time gate: `VITE_PUSH_ENABLED`. Default OFF. */
  buildEnabled: boolean;
  /**
   * Runtime hint from `GET /config` -> `push_enabled`.
   * `null` means the backend could not be asked (offline, proxy down, 500).
   */
  serverEnabled: boolean | null;
}

/** Same truthy spelling the backend accepts (`_push_enabled()` in main.py). */
export function parseFlag(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

/**
 * Decide what to do with the shell.
 *
 * - Build gate OFF  -> "disable": this build must never register a worker,
 *   so any worker left over from an earlier build is removed.
 * - Server unreachable -> "keep": we do not know, so we must not uninstall an
 *   installed app because of a network blip (defect fixed 2026-10-27).
 * - Server says OFF -> "disable": the send gate is off, tear the shell down.
 * - Both ON          -> "enable".
 */
export function decidePwaAction({
  buildEnabled,
  serverEnabled,
}: PwaSignals): PwaAction {
  if (!buildEnabled) return "disable";
  if (serverEnabled === null) return "keep";
  return serverEnabled ? "enable" : "disable";
}
