import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { apiFetch } from "./api";
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

export default function AlertCenter() {
  const { authEnabled } = useAuth();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [expanded, setExpanded] = useState(false);

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