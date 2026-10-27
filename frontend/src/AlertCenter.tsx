import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import { describePushAttempt, type PushAttempt } from "./pushStatus";
import { ALERT_NOT_FOUND_MESSAGE } from "./deepLink";
import "./AlertCenter.css";

type EscalationTier = "green" | "yellow" | "orange" | "red";

interface Alert {
  alert_id: string;
  timestamp: number;
  timestamp_iso: string;
  tier_from: EscalationTier;
  tier_to: EscalationTier;
  reason: string;
  message: string;
  status: "new" | "acknowledged" | "resolved";
  acknowledged_at: number | null;
  acknowledged_note: string | null;
  /** M5 Step 6: what the push service answered, or null when nothing was sent. */
  push?: PushAttempt | null;
}

const TIER_COLORS: Record<EscalationTier, string> = {
  green: "#7C93A8",
  yellow: "#E8A85C",
  orange: "#D47B2E",
  red: "#C2716B",
};

const TIER_REASONS: Record<string, string> = {
  repeat_threshold: "Repeated questions",
  sustained_distress: "Sustained distress",
  distress_persistence: "Distress persisted",
  danger_statement: "Danger statement",
  physical_urgent: "Physical urgent symptom",
  physical_symptom_urgent: "Physical urgent symptom",
  physical_symptom_general: "Physical unwellness",
  unresponsive: "Unresponsive",
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatAckTime(ts: number | null): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * M5 Step 6: one honest line about the push attempt for this alert.
 * Renders nothing when no attempt exists (send gate off, or older alerts).
 */
function PushLine({ push }: { push?: PushAttempt | null }) {
  const info = describePushAttempt(push);
  if (!info) return null;
  return (
    <p className={`alert-item__push alert-item__push--${info.tone}`}>
      {info.label}
    </p>
  );
}

interface FocusedTurn {
  speaker: string;
  text: string;
}

type FocusState =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "error" }
  | { status: "ok"; alert: Alert; turns: FocusedTurn[]; push?: PushAttempt | null };

export default function AlertCenter({ focusAlertId = null }: { focusAlertId?: string | null }) {
  const { authEnabled } = useAuth();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [expanded, setExpanded] = useState(Boolean(focusAlertId));
  // M5 Step 7: the alert a notification tap asked for, loaded from the
  // existing context endpoint. A missing alert (server restarted, AlertStore
  // is in-memory) must say so calmly instead of showing a blank panel.
  const [focus, setFocus] = useState<FocusState | null>(
    focusAlertId ? { status: "loading" } : null,
  );

  const fetchAlerts = async () => {
    
    try {
      const res = await apiFetch("/alerts?limit=20", {
      });
      const data = await res.json();
      setAlerts(data.alerts.reverse());
      setUnreadCount(data.unread_count);
    } catch (err) {
      console.error("Failed to fetch alerts:", err);
    }
  };

  useEffect(() => {
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 3000);
    return () => clearInterval(interval);
  }, [authEnabled]);

  useEffect(() => {
    if (!focusAlertId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(
          `/alerts/${encodeURIComponent(focusAlertId)}/context?turns=4`,
        );
        if (cancelled) return;
        if (res.status === 404) {
          setFocus({ status: "missing" });
          return;
        }
        if (!res.ok) {
          setFocus({ status: "error" });
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        // The endpoint answers 200 with `{error: "Alert not found"}` for an id
        // the in-memory AlertStore no longer has (e.g. after a restart).
        if (!data || !data.alert) {
          setFocus({ status: "missing" });
          return;
        }
        const turns: FocusedTurn[] = (data.surrounding_turns || [])
          .slice(-4)
          .map((t: any) => ({
            speaker: t?.detail?.speaker === "user" ? "Asha" : "Sahaara",
            text: String(t?.detail?.text || "").slice(0, 200),
          }))
          .filter((t: FocusedTurn) => t.text);
        setFocus({ status: "ok", alert: data.alert, turns, push: data.push });
      } catch {
        if (!cancelled) setFocus({ status: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [focusAlertId]);

  const acknowledge = async (alertId: string) => {
    
    try {
      await apiFetch(`/alerts/${alertId}/acknowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "" }),
      });
      fetchAlerts();
    } catch (err) {
      console.error("Failed to acknowledge alert:", err);
    }
  };

  const newAlerts = alerts.filter((a) => a.status === "new");
  const hasRedAlert = newAlerts.some((a) => a.tier_to === "red");

  return (
    <div className="alert-center">
      <button
        className={`alert-bell ${hasRedAlert ? "alert-bell--flashing" : ""}`}
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${unreadCount} unread alerts`}
      >
        <span className="alert-bell__icon">🔔</span>
        {unreadCount > 0 && (
          <span className="alert-bell__count">{unreadCount}</span>
        )}
      </button>

      {expanded && (
        <div className="alert-dropdown">
          <h3 className="alert-dropdown__title">
            Alerts
            {unreadCount > 0 && (
              <span className="alert-dropdown__unread">({unreadCount} new)</span>
            )}
          </h3>

          {focus && (
            <div className={`alert-focus alert-focus--${focus.status}`}>
              {focus.status === "loading" && (
                <p className="alert-focus__text">Loading the alert…</p>
              )}
              {focus.status === "missing" && (
                <p className="alert-focus__text">{ALERT_NOT_FOUND_MESSAGE}</p>
              )}
              {focus.status === "error" && (
                <p className="alert-focus__text">
                  That alert could not be loaded. The dashboard below still
                  works.
                </p>
              )}
              {focus.status === "ok" && (
                <>
                  <div className="alert-focus__head">
                    <span
                      className="alert-focus__tier"
                      style={{ color: TIER_COLORS[focus.alert.tier_to] }}
                    >
                      {focus.alert.tier_to.toUpperCase()}
                    </span>
                    <span className="alert-focus__time">
                      {formatTime(focus.alert.timestamp_iso)}
                    </span>
                  </div>
                  <p className="alert-focus__message">{focus.alert.message}</p>
                  <PushLine push={focus.push} />
                  {focus.turns.length > 0 && (
                    <ul className="alert-focus__turns">
                      {focus.turns.map((turn, index) => (
                        <li key={index}>
                          <strong>{turn.speaker}:</strong> {turn.text}
                        </li>
                      ))}
                    </ul>
                  )}
                  <button
                    className="alert-focus__dismiss"
                    onClick={() => setFocus(null)}
                  >
                    Dismiss
                  </button>
                </>
              )}
            </div>
          )}

          {alerts.length === 0 && (
            <p className="alert-dropdown__empty">No alerts yet.</p>
          )}

          <div className="alert-dropdown__list">
            {alerts.map((alert) => (
              <div
                key={alert.alert_id}
                className={`alert-item ${
                  alert.tier_to === "red" && alert.status === "new"
                    ? "alert-item--red"
                    : ""
                }`}
                style={{
                  borderLeftColor: TIER_COLORS[alert.tier_to],
                }}
              >
                <div className="alert-item__header">
                  <span
                    className="alert-item__tier"
                    style={{ color: TIER_COLORS[alert.tier_to] }}
                  >
                    {alert.tier_to.toUpperCase()}
                  </span>
                  <span className="alert-item__time">
                    {formatTime(alert.timestamp_iso)}
                  </span>
                  <span
                    className={`alert-item__status alert-item__status--${alert.status}`}
                  >
                    {alert.status === "new"
                      ? "● New"
                      : alert.status === "acknowledged"
                      ? "✓ Seen"
                      : "✓ Resolved"}
                  </span>
                </div>

                <p className="alert-item__reason">
                  {TIER_REASONS[alert.reason] || alert.reason}
                </p>

                <p className="alert-item__message">{alert.message}</p>

                <PushLine push={alert.push} />

                {alert.status === "acknowledged" && alert.acknowledged_at && (
                  <p className="alert-item__ack-info">
                    Seen by Rohan at {formatAckTime(alert.acknowledged_at)}
                    {alert.acknowledged_note
                      ? ` — "${alert.acknowledged_note}"`
                      : ""}
                  </p>
                )}

                {alert.status === "new" && (
                  <button
                    className="alert-item__ack-btn"
                    onClick={() => acknowledge(alert.alert_id)}
                  >
                    Acknowledge
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}