import { useEffect, useRef, useState } from "react";
import "./EscalationLadder.css";

type EscalationTier = "green" | "yellow" | "orange" | "red";

interface EscalationStatus {
  tier: EscalationTier;
  tier_duration_seconds: number;
  yellow_reason: string | null;
  yellow_duration_seconds: number | null;
  distress_duration_seconds: number | null;
  last_repeat_count: number;
  repeat_threshold: number;
  demo_mode: boolean;
}

interface Event {
  timestamp: number;
  timestamp_iso: string;
  type: string;
  profile_id: string;
  detail: Record<string, any>;
}

interface Alert {
  timestamp: number;
  timestamp_iso: string;
  tier_from: EscalationTier;
  tier_to: EscalationTier;
  reason: string;
  profile_id: string;
  message: string;
  detail: Record<string, any>;
}

const TIER_ORDER: EscalationTier[] = ["green", "yellow", "orange", "red"];

const TIER_LABELS: Record<EscalationTier, string> = {
  green: "GREEN",
  yellow: "YELLOW",
  orange: "ORANGE",
  red: "RED",
};

const TIER_REASONS: Record<string, string> = {
  repeat_threshold: "Repeated questions",
  sustained_distress: "Sustained distress",
  distress_persistence: "Distress persisted",
  step_down: "Distress cleared",
  danger_statement: "Danger statement",
  unresponsive: "Unresponsive",
  manual_override: "Manual override",
  session_start: "Session started",
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  session_start: "#e8e4dc",
  repeat: "#d4e6f1",
  distress: "#fef3c7",
  tier_change: "#fce7f3",
  alert: "#fef2f2",
  consent_asked: "#e0e7ff",
  consent_resolved: "#dcfce7",
  fallback: "#fef2f2",
  danger_statement: "#fef2f2",
  system_health: "#e0e7ff",
};

const EVENT_TYPE_TEXT_COLORS: Record<string, string> = {
  session_start: "#6b6660",
  repeat: "#2c6fad",
  distress: "#b45309",
  tier_change: "#be185d",
  alert: "#991b1b",
  consent_asked: "#3730a3",
  consent_resolved: "#166534",
  fallback: "#991b1b",
  danger_statement: "#991b1b",
  system_health: "#3730a3",
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function getEventDetailText(event: Event): string {
  const d = event.detail;
  switch (event.type) {
    case "tier_change":
      return `${d.old_tier} → ${d.new_tier} (${d.reason})`;
    case "repeat":
      return `${d.question} (x${d.count})`;
    case "distress":
      return `sustained=${d.sustained}, emotion=${d.emotion}`;
    case "alert":
      return d.message || d.reason;
    case "consent_asked":
      return `Asked: ${d.question}`;
    case "consent_resolved":
      return `Outcome: ${d.outcome} for ${d.contact_name}`;
    case "fallback":
      return `Type: ${d.fallback_type}`;
    case "danger_statement":
      return `Phrase: ${d.phrase}`;
    case "system_health":
      return d.message;
    case "consent_asked":
      return `Asked to call ${d.contact_name}`;
    default:
      return JSON.stringify(d).slice(0, 100);
  }
}

export default function EscalationLadder() {
  const [status, setStatus] = useState<EscalationStatus | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [sseConnected, setSseConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch initial data
  const fetchData = async () => {
    try {
      const [statusRes, eventsRes] = await Promise.all([
        fetch("http://127.0.0.1:8000/escalation/status"),
        fetch("http://127.0.0.1:8000/escalation/events?limit=50"),
      ]);
      const statusData = await statusRes.json();
      const eventsData = await eventsRes.json();
      setStatus(statusData);
      setEvents(eventsData.events.reverse()); // newest first
    } catch (err) {
      console.error("Failed to fetch escalation data:", err);
    }
  };

  // Connect to SSE for real-time updates
  useEffect(() => {
    fetchData();

    // Connect to SSE endpoint
    const es = new EventSource("http://127.0.0.1:8000/stream/events");
    esRef.current = es;

    es.onopen = () => setSseConnected(true);
    es.onerror = () => setSseConnected(false);

    es.addEventListener("status", (e) => {
      try {
        const data = JSON.parse(e.data);
        setStatus(data);
      } catch {}
    });

    es.addEventListener("tier_change", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "tier_change" }, ...prev].slice(0, 50));
        // Also update status from tier_change
        setStatus((prev) => prev ? { ...prev, tier: data.new_tier as EscalationTier } : null);
      } catch {}
    });

    es.addEventListener("alert", (e) => {
      try {
        const data = JSON.parse(e.data);
        setAlerts((prev) => [data, ...prev].slice(0, 20));
        setEvents((prev) => [{ ...data, type: "alert" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("consent_asked", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "consent_asked" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("consent_resolved", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "consent_resolved" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("repeat", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "repeat" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("distress", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "distress" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("system_health", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "system_health" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("danger_statement", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "danger_statement" }, ...prev].slice(0, 50));
      } catch {}
    });

    es.addEventListener("fallback", (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "fallback" }, ...prev].slice(0, 50));
      } catch {}
    });

    // Initial fetch
    fetchData();

    return () => {
      es.close();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  if (!status) {
    return (
      <div className="escalation-ladder">
        <div className="ladder-tier" style={{ opacity: 0.5 }}>
          <div className="tier-indicator">?</div>
          <div className="tier-info">
            <div className="tier-name">Loading...</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div>
          <h1 className="dashboard-title">Caregiver Dashboard</h1>
          <p className="dashboard-subtitle">Rohan's view — Asha's session</p>
        </div>
        <div className="connection-status">
          <span className={`connection-dot ${sseConnected ? "connected" : ""}`} />
          <span>{sseConnected ? "Live (SSE)" : "Connecting..."}</span>
        </div>
      </div>

      {/* Status Cards */}
      <div className="status-panel">
        <div className="status-card">
          <div className="status-label">Current Tier</div>
          <div className={`status-value tier-${status.tier}`}>
            {TIER_LABELS[status.tier]}
          </div>
        </div>
        <div className="status-card">
          <div className="status-label">Time in Tier</div>
          <div className="status-value">{formatDuration(status.tier_duration_seconds)}</div>
        </div>
        <div className="status-card">
          <div className="status-label">Repeat Count</div>
          <div className="status-value">
            {status.last_repeat_count} / {status.repeat_threshold}
          </div>
        </div>
        <div className="status-card">
          <div className="status-label">Distress Duration</div>
          <div className="status-value">
            {formatDuration(status.distress_duration_seconds)}
          </div>
        </div>
      </div>

      {/* Escalation Ladder */}
      <div className="escalation-ladder">
        {TIER_ORDER.map((tier) => (
          <div
            key={tier}
            className={`ladder-tier ${tier} ${status.tier === tier ? "active" : ""}`}
          >
            <div className="tier-indicator">
              {TIER_ORDER.indexOf(tier) + 1}
            </div>
            <div className="tier-pulse" />
            <div className="tier-info">
              <div className="tier-name">{TIER_LABELS[tier]}</div>
              {status.tier === tier && status.yellow_reason && (
                <div className="tier-reason">
                  Reason: {TIER_REASONS[status.yellow_reason] || status.yellow_reason}
                </div>
              )}
              {status.tier === tier && status.yellow_duration_seconds && (
                <div className="tier-duration">
                  Tier held: {formatDuration(status.yellow_duration_seconds)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Alert Feed */}
      <div style={{ marginTop: 20 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, color: "#2d2a26" }}>
          Alerts ({alerts.length})
        </h3>
        <div className="event-feed" style={{ maxHeight: 200 }}>
          {alerts.length === 0 ? (
            <div style={{ padding: 20, textAlign: "center", color: "#999" }}>
              No alerts yet
            </div>
          ) : (
            alerts.map((alert, idx) => (
              <div key={idx} className="event-item" style={{ borderLeft: "4px solid #E8A85C" }}>
                <span className="event-time">{formatTime(alert.timestamp_iso)}</span>
                <span className="event-type" style={{ background: "#fef2f2", color: "#991b1b" }}>
                  ALERT
                </span>
                <span className="event-detail" style={{ fontWeight: 500 }}>
                  {alert.message}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Event Feed */}
      <div style={{ marginTop: 20 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, color: "#2d2a26" }}>
          Session Events ({events.length})
        </h3>
        <div className="event-feed">
          {events.length === 0 ? (
            <div style={{ padding: 20, textAlign: "center", color: "#999" }}>
              No events yet
            </div>
          ) : (
            events.map((event, idx) => {
              const bgColor = EVENT_TYPE_COLORS[event.type] || "#faf9f6";
              const textColor = EVENT_TYPE_TEXT_COLORS[event.type] || "#4a4642";
              return (
                <div key={idx} className="event-item" style={{ background: bgColor, borderLeft: `4px solid ${textColor}` }}>
                  <span className="event-time">{formatTime(event.timestamp_iso)}</span>
                  <span className="event-type" style={{ background: bgColor, color: textColor }}>
                    {event.type.toUpperCase()}
                  </span>
                  <span className="event-detail" style={{ color: textColor }}>
                    {getEventDetailText(event)}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* System Health */}
      <div style={{ marginTop: 20 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, color: "#2d2a26" }}>
          System Health
        </h3>
        <div className="event-feed" style={{ maxHeight: 150 }}>
          {events
            .filter(e => e.type === "fallback" || e.type === "system_health")
            .map((event, idx) => (
              <div key={idx} className="event-item" style={{ background: "#e0e7ff", borderLeft: "4px solid #3730a3" }}>
                <span className="event-time">{formatTime(event.timestamp_iso)}</span>
                <span className="event-type" style={{ background: "#e0e7ff", color: "#3730a3" }}>
                  {event.type.toUpperCase()}
                </span>
                <span className="event-detail" style={{ color: "#3730a3" }}>
                  {getEventDetailText(event)}
                </span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}