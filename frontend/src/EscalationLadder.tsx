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

interface EventsResponse {
  events: Event[];
  total: number;
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

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function EscalationLadder() {
  const [status, setStatus] = useState<EscalationStatus | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<number>();

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

  // Connect to WebSocket for real-time updates
  useEffect(() => {
    fetchData();

    // REST polling fallback (more reliable than WS for this demo)
    pollRef.current = window.setInterval(fetchData, 2000);

    // Try WebSocket for real-time tier changes
    try {
      const ws = new WebSocket("ws://127.0.0.1:8000/ws/escalation");
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => setConnected(false);
      ws.onerror = () => setConnected(false);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "status" && data.data) {
            setStatus(data.data);
          } else if (data.timestamp) {
            // New event
            setEvents((prev) => [data, ...prev].slice(0, 50));
          }
        } catch {
          // ignore parse errors
        }
      };
    } catch {
      // WS failed, rely on polling
    }

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      wsRef.current?.close();
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
          <span className={`connection-dot ${connected ? "connected" : ""}`} />
          <span>{connected ? "Live" : "Polling"}</span>
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
            events.map((event, idx) => (
              <div key={idx} className="event-item">
                <span className="event-time">{formatTime(event.timestamp_iso)}</span>
                <span className={`event-type ${event.type}`}>{event.type}</span>
                <span className="event-detail">
                  {JSON.stringify(event.detail).slice(0, 100)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}