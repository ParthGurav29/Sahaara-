import { useCallback, useEffect, useState } from "react";
import {
  BUILD_ENABLED,
  alertsState,
  disableAlerts,
  enableAlerts,
  readPushConfig,
  type AlertsState,
} from "./pushAlerts";
import { isIos, isStandalone } from "./pushSupport";
import "./PushButton.css";

/**
 * Caregiver "Enable alerts" control (M5 Step 4).
 *
 * Renders nothing unless the build gate is ON **and** `GET /config` reports
 * `push_enabled: true`. Permission is only ever requested from the click
 * handler — never on mount, never on a timer.
 */
export default function PushButton() {
  const [state, setState] = useState<AlertsState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!BUILD_ENABLED) return;

    let cancelled = false;
    (async () => {
      const config = await readPushConfig();
      if (cancelled) return;
      if (!config?.enabled) {
        setState(null);
        return;
      }
      const next = await alertsState();
      if (!cancelled) setState(next);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const handleClick = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const next =
        state === "enabled" ? await disableAlerts() : await enableAlerts();
      setState(next);
    } finally {
      setBusy(false);
    }
  }, [busy, state]);

  if (!BUILD_ENABLED || state === null) return null;

  const note = (
    <p className="push-alerts-note">
      Best-effort only — Android may delay or block notifications. This
      dashboard is the record.
    </p>
  );

  if (state === "unsupported") {
    return (
      <div className="push-alerts" aria-live="polite">
        <span className="push-alerts-status">Alerts aren't available in this browser.</span>
        {note}
      </div>
    );
  }

  if (state === "needs-install") {
    // iOS only exposes push to an installed web app. Implemented and
    // unit-tested, but M5 is Android-only: not device-tested, not claimed.
    return (
      <div className="push-alerts" aria-live="polite">
        <span className="push-alerts-status">
          Add Sahaara to your home screen to turn on alerts.
        </span>
        {note}
      </div>
    );
  }

  if (state === "denied") {
    return (
      <div className="push-alerts" aria-live="polite">
        <button className="push-alerts-button" type="button" disabled>
          Alerts blocked
        </button>
        <span className="push-alerts-status">
          Unblock notifications for this site in Chrome settings, then reload.
        </span>
        {note}
      </div>
    );
  }

  return (
    <div className="push-alerts" aria-live="polite">
      <button
        className="push-alerts-button"
        type="button"
        onClick={handleClick}
        disabled={busy}
      >
        {busy
          ? "Working…"
          : state === "enabled"
            ? "Turn off alerts"
            : state === "error"
              ? "Try again"
              : "Enable alerts"}
      </button>
      {state === "enabled" && <span className="push-alerts-status">Alerts on</span>}
      {state === "enabled" && !isIos() && !isStandalone() && (
        <span className="push-alerts-status push-alerts-hint">
          For the most reliable delivery, install Sahaara to your home screen —
          a Chrome tab may delay or block alerts.
        </span>
      )}
      {state === "error" && (
        <span className="push-alerts-status push-alerts-error">
          Couldn't turn on alerts. Check notifications in Chrome settings.
        </span>
      )}
      {note}
    </div>
  );
}
