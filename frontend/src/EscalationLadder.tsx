import { useEffect, useRef, useState } from "react";
import "./EscalationLadder.css";
import AlertCenter from "./AlertCenter";

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
  conversation_turn: "#f5f2ed",
  memory_box_played: "#e8f5e9",
  sahaara_initiative: "#e8f0fe",
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
  conversation_turn: "#5c5650",
  memory_box_played: "#2e7d32",
  sahaara_initiative: "#1a56db",
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
    case "conversation_turn":
      return `${d.speaker === "user" ? "Asha" : "Sahaara"}: ${(d.text || "").slice(0, 80)}`;
    case "memory_box_played":
      return `Played: ${d.clip_filename} (${d.triggered_by})`;
    case "sahaara_initiative":
      return `${d.text?.slice(0, 80) || ""}`;
    default:
      return JSON.stringify(d).slice(0, 100);
  }
}

function TierRow({ tier, isActive, status }: { tier: EscalationTier; isActive: boolean; status: EscalationStatus }) {
  const tierIndex = TIER_ORDER.indexOf(tier);
  return (
    <div
      className={`tier-row ${tier} ${isActive ? "active" : ""}`}
      style={{ "--tier-color": getTierColor(tier) } as React.CSSProperties}
    >
      <span className="tier-num">{tierIndex + 1}</span>
      <span className="tier-dot" />
      <span className="tier-label">{TIER_LABELS[tier]}</span>
      {isActive && status.yellow_reason && (
        <span className="tier-reason">{TIER_REASONS[status.yellow_reason] || status.yellow_reason}</span>
      )}
      {isActive && status.yellow_duration_seconds && (
        <span className="tier-duration">{formatDuration(status.yellow_duration_seconds)}</span>
      )}
    </div>
  );
}

function getTierColor(tier: EscalationTier): string {
  switch (tier) {
    case "green": return "#7C93A8";
    case "yellow": return "#E8A85C";
    case "orange": return "#D47B2E";
    case "red": return "#C2716B";
  }
}

function StatusCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="status-card">
      <span className="status-label">{label}</span>
      <span className="status-value" style={{ color: color || "#2d2a26" }}>{value}</span>
    </div>
  );
}

function EventItem({ event, isAlert = false }: { event: Event | Alert; isAlert?: boolean }) {
  const e = event as Event;
  const bgColor = isAlert ? "#fef2f2" : EVENT_TYPE_COLORS[e.type] || "#faf9f6";
  const textColor = isAlert ? "#991b1b" : EVENT_TYPE_TEXT_COLORS[e.type] || "#4a4642";
  const time = formatTime(isAlert ? (event as Alert).timestamp_iso : e.timestamp_iso);
  const typeLabel = isAlert ? "ALERT" : e.type.toUpperCase();
  const detail = isAlert ? (event as Alert).message : getEventDetailText(e);
  
  return (
    <div className="event-item-compact" style={{ background: bgColor, borderLeftColor: textColor }}>
      <span className="event-time">{time}</span>
      <span className="event-type-compact" style={{ background: bgColor, color: textColor }}>{typeLabel}</span>
      <span className="event-detail-compact" style={{ color: textColor }}>{detail}</span>
    </div>
  );
}

export default function EscalationLadder() {
  const [status, setStatus] = useState<EscalationStatus | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [sseConnected, setSseConnected] = useState(false);
  const [whyExplanation, setWhyExplanation] = useState<string | null>(null);
  const [schedulerPaused, setSchedulerPaused] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const fetchWhyContext = async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/turn/context");
      const data = await res.json();
      if (data.explanation) {
        setWhyExplanation(data.explanation);
      }
    } catch {}
  };

  const fetchData = async () => {
    try {
      const [statusRes, eventsRes] = await Promise.all([
        fetch("http://127.0.0.1:8000/escalation/status"),
        fetch("http://127.0.0.1:8000/escalation/events?limit=20"),
      ]);
      const statusData = await statusRes.json();
      const eventsData = await eventsRes.json();
      setStatus(statusData);
      setEvents(eventsData.events.reverse());
    } catch (err) {
      console.error("Failed to fetch escalation data:", err);
    }
  };

  useEffect(() => {
    fetchData();

    const es = new EventSource("http://127.0.0.1:8000/stream/events");
    esRef.current = es;

    es.onopen = () => setSseConnected(true);
    es.onerror = () => setSseConnected(false);

    const handlers: Record<string, (e: MessageEvent) => void> = {
      status: (e) => { try { setStatus(JSON.parse(e.data)); } catch {} },
      tier_change: (e) => {
        try {
          const data = JSON.parse(e.data);
          setEvents((prev) => [{ ...data, type: "tier_change" }, ...prev].slice(0, 40));
          setStatus((prev) => prev ? { ...prev, tier: data.detail.new_tier as EscalationTier } : null);
        } catch {}
      },
      alert: (e) => {
        try {
          const data = JSON.parse(e.data);
          setAlerts((prev) => [data, ...prev].slice(0, 10));
          setEvents((prev) => [{ ...data, type: "alert" }, ...prev].slice(0, 40));
        } catch {}
      },
      consent_asked: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "consent_asked" }, ...prev].slice(0, 40)); } catch {} },
      consent_resolved: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "consent_resolved" }, ...prev].slice(0, 40)); } catch {} },
      repeat: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "repeat" }, ...prev].slice(0, 40)); } catch {} },
      distress: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "distress" }, ...prev].slice(0, 40)); } catch {} },
      system_health: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "system_health" }, ...prev].slice(0, 40)); } catch {} },
      danger_statement: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "danger_statement" }, ...prev].slice(0, 40)); } catch {} },
      fallback: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "fallback" }, ...prev].slice(0, 40)); } catch {} },
      conversation_turn: (e) => {
        try {
          const data = JSON.parse(e.data);
          setEvents((prev) => [{ ...data, type: "conversation_turn" }, ...prev].slice(0, 40));
          if (data.detail?.speaker === "sahaara") {
            fetchWhyContext();
          }
        } catch {}
      },
      memory_box_played: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "memory_box_played" }, ...prev].slice(0, 40)); } catch {} },
      sahaara_initiative: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "sahaara_initiative" }, ...prev].slice(0, 40)); } catch {} },
    };

    Object.entries(handlers).forEach(([type, handler]) => es.addEventListener(type, handler));

    return () => {
      Object.entries(handlers).forEach(([type, handler]) => es.removeEventListener(type, handler));
      es.close();
    };
  }, []);

  if (!status) {
    return (
      <div className="escalation-ladder-minimal">
        <div className="tier-row loading">Loading...</div>
      </div>
    );
  }

  const recentEvents = events.slice(0, 10);
  const recentAlerts = alerts.slice(0, 3);

  return (
    <div className="escalation-ladder-minimal">
      {/* Header */}
      <div className="dashboard-header-minimal">
        <div>
          <h1 className="dashboard-title-minimal">Caregiver Dashboard</h1>
          <p className="dashboard-subtitle-minimal">Asha's session</p>
        </div>
        <div className="connection-status-minimal">
          <span className={`connection-dot-minimal ${sseConnected ? "connected" : ""}`} />
          <span>{sseConnected ? "Live" : "Connecting"}</span>
        </div>
        <AlertCenter />
      </div>

      {/* Status Cards */}
      <div className="status-panel-minimal">
        <StatusCard label="Tier" value={formatDuration(status.tier_duration_seconds)} color={getTierColor(status.tier)} />
        <StatusCard label="Repeats" value={`${status.last_repeat_count} / ${status.repeat_threshold}`} />
        <StatusCard label="Distress" value={formatDuration(status.distress_duration_seconds)} />
      </div>

      {/* Tier Ladder - compact */}
      <div className="tier-ladder-minimal">
        {TIER_ORDER.map((tier) => (
          <TierRow key={tier} tier={tier} isActive={status.tier === tier} status={status} />
        ))}
      </div>

      {/* Caregiver Controls */}
      <div className="caregiver-controls-minimal">
        <button
          className="control-btn control-btn--reset"
          onClick={async () => {
            try {
              await fetch("http://127.0.0.1:8000/demo/reset", { method: "POST" });
              window.location.reload();
            } catch (err) {
              console.error("Reset failed:", err);
            }
          }}
          title="Reset demo state"
        >
          ↺ Reset Demo
        </button>
        <button
          className={`control-btn ${schedulerPaused ? "control-btn--paused" : "control-btn--active"}`}
          onClick={async () => {
            try {
              const res = await fetch("http://127.0.0.1:8000/scheduler/toggle", { method: "POST" });
              const data = await res.json();
              setSchedulerPaused(data.paused);
            } catch {}
          }}
          title={schedulerPaused ? "Reminders paused" : "Reminders active"}
        >
          {schedulerPaused ? "▶ Reminders Off" : "⏸ Reminders On"}
        </button>
      </div>

      {/* Day Timeline */}
      <div className="feed-section-minimal">
        <h3 className="feed-title">Day Timeline</h3>
        <div className="feed-list-minimal">
          {recentAlerts.length > 0 && recentAlerts.map((alert, idx) => (
            <EventItem key={idx} event={alert} isAlert />
          ))}
          {recentEvents.length > 0 && recentEvents.map((event, idx) => (
            <EventItem key={idx} event={event} />
          ))}
          {recentAlerts.length === 0 && recentEvents.length === 0 && (
            <div className="feed-empty">No events yet</div>
          )}
        </div>
      </div>

      {/* Why did Sahaara say this? */}
      {whyExplanation && (
        <div className="why-panel-minimal">
          <h4 className="why-panel__title">Why did Sahaara say this?</h4>
          <p className="why-panel__text">{whyExplanation}</p>
        </div>
      )}
    </div>
  );
}