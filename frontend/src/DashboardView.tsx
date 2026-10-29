import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "./api";
import { subscribeEvents, subscribeConnection } from "./eventStream";
import { describePushAttempt } from "./pushStatus";
import { useVoiceActions } from "./VoiceStateContext";
import { useAuth } from "./AuthContext";
import { navigateTo } from "./useRoute";
import "./DashboardView.css";

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
  alert_id: string;
  timestamp: number;
  timestamp_iso: string;
  tier_from: EscalationTier;
  tier_to: EscalationTier;
  reason: string;
  profile_id: string;
  message: string;
  detail: Record<string, any>;
  status?: string;
  acknowledged_at?: number;
  acknowledged_note?: string;
}

interface Suggestion {
  suggestion_id: string;
  type: string;
  suggested_field: string;
  suggested_value: string;
  source_turn: string;
}

const TIER_ORDER: EscalationTier[] = ["green", "yellow", "orange", "red"];
const TIER_LABELS: Record<EscalationTier, string> = {
  green: "GREEN",
  yellow: "YELLOW",
  orange: "ORANGE",
  red: "RED",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  alert_sent: "PHONE ALERT",
  action: "TASK",
  reminder_fired: "TIMER / REMINDER",
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
  alert_sent: "#eef2f7",
  action: "#eef2f0",
  reminder_fired: "#eef2f7",
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
  alert_sent: "#475569",
  action: "#3f5d4e",
  reminder_fired: "#475569",
};

const FIELD_LABELS: Record<string, string> = {
  "likes.music": "Music they like",
  "likes.topics": "Topics they enjoy",
  "likes.food": "Food/drink they like",
  key_events: "Life story event",
  daily_routine: "Daily routine",
  family: "Person mentioned",
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

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
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
      return d.contact_name ? `Asked to call ${d.contact_name}` : `Asked: ${d.question}`;
    case "consent_answered":
    case "consent_resolved":
      return `Outcome: ${d.outcome} for ${d.contact_name}`;
    case "fallback":
      return `Type: ${d.fallback_type}`;
    case "danger_statement":
      return `Phrase: ${d.phrase}`;
    case "physical_symptom":
      return `${d.phrase} — ${d.urgency}`;
    case "system_health":
      return d.message;
    case "session_start":
      return "Session started";
    case "session_end":
      return "Session ended";
    case "alert_acknowledged":
      return d.note ? `Alert acknowledged — "${d.note}"` : "Alert acknowledged";
    case "conversation_turn":
      return `${d.speaker === "user" ? "Asha" : "Sahaara"}: ${(d.text || "").slice(0, 80)}`;
    case "memory_box_played":
      return `Played: ${d.clip_filename} (${d.triggered_by})`;
    case "sahaara_initiative":
      return `${d.text?.slice(0, 80) || ""}`;
    case "alert_sent":
      return describePushAttempt(d)?.label || "Phone alert recorded";
    case "action": {
      const name = String(d.action_type || "task").replace(/_/g, " ");
      const status = d.success === false ? " (not done)" : "";
      const base = `${name}${status}: ${d.spoken || ""}`;
      const heard = String(d.raw_text || "").trim();
      return heard ? `${base} · heard: "${heard.slice(0, 70)}"` : base;
    }
    case "reminder_fired":
      return d.spoken || `${d.kind || "timer"} fired`;
    default: {
      const text = d.message || d.reason || d.text;
      if (typeof text === "string" && text.trim()) return text.slice(0, 100);
      return event.type.replace(/_/g, " ").toLowerCase();
    }
  }
}

function TierRow({ tier, isActive }: { tier: EscalationTier; isActive: boolean }) {
  const tierColors: Record<EscalationTier, string> = {
    green: "var(--sage)",
    yellow: "var(--amber)",
    orange: "var(--orange)",
    red: "var(--red)",
  };
  return (
    <div
      className={`rung ${isActive ? "on" : ""}`}
      style={{ borderBottomColor: isActive ? tierColors[tier] : "transparent" } as React.CSSProperties}
    >
      {TIER_LABELS[tier]}
    </div>
  );
}

function StatusCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="stat">
      <small>{label}</small>
      <div style={{ color: color || "var(--ink)" } as React.CSSProperties}>{value}</div>
    </div>
  );
}

function EventItem({ event, isAlert = false }: { event: Event | Alert; isAlert?: boolean }) {
  const e = event as Event;
  const bgColor = isAlert ? "#fef2f2" : EVENT_TYPE_COLORS[e.type] || "#faf9f6";
  const textColor = isAlert ? "#991b1b" : EVENT_TYPE_TEXT_COLORS[e.type] || "#4a4642";
  const time = formatTime(isAlert ? (event as Alert).timestamp_iso : e.timestamp_iso);
  const typeLabel = isAlert ? "ALERT" : EVENT_TYPE_LABELS[e.type] || e.type.toUpperCase();
  const detail = isAlert ? (event as Alert).message : getEventDetailText(e);

  return (
    <div className="event-item-compact" style={{ background: bgColor, borderLeftColor: textColor } as React.CSSProperties}>
      <span className="event-time">{time}</span>
      <span className="event-type-compact" style={{ background: bgColor, color: textColor } as React.CSSProperties}>{typeLabel}</span>
      <span className="event-detail-compact" style={{ color: textColor } as React.CSSProperties}>{detail}</span>
    </div>
  );
}

function AlertCard({ alert, onAcknowledge }: { alert: Alert; onAcknowledge: () => void }) {
  const tierColors: Record<EscalationTier, string> = {
    green: "var(--sage)",
    yellow: "var(--amber)",
    orange: "var(--orange)",
    red: "var(--red)",
  };
  const handleClick = () => {
    console.log("AlertCard Acknowledge clicked for:", alert.alert_id);
    onAcknowledge();
  };
  return (
    <div className="card alert">
      <span className="dot" style={{ background: tierColors[alert.tier_to] } as React.CSSProperties} />
      <div>
        <h3>{TIER_LABELS[alert.tier_to]} · {alert.message}</h3>
        <p className="muted">{alert.detail?.reason || ""}</p>
        <small className="muted">{formatDate(alert.timestamp_iso)} · {formatTime(alert.timestamp_iso)}</small>
      </div>
      <button className="btn sm ghost" onClick={handleClick}>Acknowledge</button>
    </div>
  );
}

function SuggestionCard({ suggestion, onApprove, onReject }: { suggestion: Suggestion; onApprove: () => void; onReject: () => void }) {
  const fieldLabel = FIELD_LABELS[suggestion.suggested_field] || suggestion.suggested_field;
  return (
    <div className="rrow" id={`rr-${suggestion.suggestion_id}`}>
      <span>
        <strong>"{suggestion.suggested_value}"</strong>
        <br />
        <span className="muted">→ {fieldLabel}</span>
      </span>
      <span>
        <button className="btn sm" onClick={onApprove}>Approve</button>
        <button className="btn sm ghost" onClick={onReject}>Reject</button>
      </span>
    </div>
  );
}

const PROFILE_SECTIONS = [
  {
    id: "basics",
    title: "Basics",
    fields: [
      { key: "preferred_name", label: "Preferred name", shared: true },
      { key: "language_mix", label: "Language mix", shared: true },
      { key: "age", label: "Age", shared: true },
      { key: "city", label: "City", shared: true },
    ],
  },
  {
    id: "family",
    title: "Family and contacts",
    fields: [
      { key: "name", label: "Name", shared: true },
      { key: "relationship", label: "Relationship", shared: true },
      { key: "living_status", label: "Status", shared: true },
      { key: "phone", label: "Phone", shared: false },
      { key: "may_offer_notify", label: "May offer notify", shared: true },
      { key: "notify_order", label: "Notify order", shared: true },
    ],
  },
  {
    id: "life_story",
    title: "Life story",
    fields: [
      { key: "career", label: "Career", shared: true },
      { key: "key_memories", label: "Key memories", shared: true },
      { key: "proud_moments", label: "Proud moments", shared: true },
    ],
  },
  {
    id: "likes",
    title: "Likes and what soothes her",
    fields: [
      { key: "music", label: "Music and food", shared: true },
      { key: "calming_strategies", label: "What calms her when anxious", shared: true },
    ],
  },
  {
    id: "avoid",
    title: "Sensitive topics",
    fields: [
      { key: "topics", label: "Topics to avoid or redirect", shared: true },
      { key: "when_asking_for_deceased", label: "If she asks for someone who has died", shared: true },
    ],
  },
  {
    id: "daily_routine",
    title: "Daily routine",
    fields: [
      { key: "wake_time", label: "Wake time", shared: true },
      { key: "meals", label: "Meals", shared: true },
      { key: "sleep_time", label: "Sleep time", shared: true },
      { key: "restless_window", label: "Restless window", shared: true },
      { key: "medication_times", label: "Medication times (reminders only)", shared: false },
    ],
  },
  {
    id: "health_notes",
    title: "Health notes (caregiver record)",
    note: "Kept private. Never sent to Sahaara's conversation prompt.",
    fields: [
      { key: "conditions", label: "Conditions and allergies", shared: false },
      { key: "medications", label: "Medication times (reminders only)", shared: false },
      { key: "doctor_contact", label: "Doctor, hearing, vision, mobility", shared: false },
    ],
  },
  {
    id: "communication_tips",
    title: "Communication tips",
    fields: [
      { key: "tips", label: "What works with her", shared: true },
    ],
  },
];

interface ProfileData {
  basics: Record<string, any>;
  family: Array<Record<string, any>>;
  life_story: Record<string, any>;
  likes: Record<string, any>;
  avoid: Record<string, any>;
  daily_routine: Record<string, any>;
  health_notes: Record<string, any>;
  communication_tips: Record<string, any>;
  [key: string]: any;
}

interface SectionConfig {
  id: string;
  title: string;
  note?: string;
  fields: Array<{ key: string; label: string; shared: boolean }>;
}

export default function DashboardView() {
  const { setTier } = useVoiceActions();
  const { logout } = useAuth();
  const [status, setStatus] = useState<EscalationStatus | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionsCount, setSuggestionsCount] = useState(0);
  const [dailySummary, setDailySummary] = useState<string | null>(null);
  const [whyExplanation, setWhyExplanation] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"today" | "alerts" | "profile">("today");
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<"saved" | "error" | null>(null);
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});

  const fetchWhyContext = useCallback(async () => {
    try {
      const res = await apiFetch("/turn/context");
      const data = await res.json();
      if (data.explanation) setWhyExplanation(data.explanation);
    } catch {}
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, eventsRes] = await Promise.all([
        apiFetch("/escalation/status"),
        apiFetch("/escalation/events?limit=20"),
      ]);
      const statusData = await statusRes.json();
      const eventsData = await eventsRes.json();
      setStatus(statusData);
      if (statusData?.tier) setTier(statusData.tier as EscalationTier);
      setEvents(eventsData.events.reverse());
    } catch (err) {
      console.error("Failed to fetch escalation data:", err);
    }
  }, [setTier]);

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await apiFetch("/alerts?limit=20");
      console.log("Fetch alerts response:", res.status);
      const data = await res.json();
      console.log("Fetch alerts data:", data);
      setAlerts(data.alerts || []);
    } catch (err) {
      console.error("Failed to fetch alerts:", err);
    }
  }, []);

  const fetchDailySummary = useCallback(async () => {
    try {
      const res = await apiFetch("/daily-summary");
      console.log("Daily summary response:", res.status);
      const data = await res.json();
      console.log("Daily summary data:", data);
      // Backend returns { summary_text, ... } or just the summary object
      const summary = data.summary_text || data.summary || data.text;
      setDailySummary(summary || "No summary available");
    } catch (err) {
      console.error("Failed to fetch daily summary:", err);
    }
  }, []);

  const fetchSuggestions = useCallback(async () => {
    try {
      const res = await apiFetch("/suggestions");
      const data = await res.json();
      const suggs = data.suggestions || [];
      setSuggestions(suggs);
      setSuggestionsCount(suggs.length);
    } catch (err) {
      console.error("Failed to fetch suggestions:", err);
    }
  }, []);

  const fetchProfile = useCallback(async () => {
    try {
      const res = await apiFetch("/profile/current");
      const data = await res.json();
      if (data.data) setProfile(data.data);
    } catch (err) {
      console.error("Failed to fetch profile:", err);
    }
  }, []);

  const saveProfile = async () => {
    if (!profile) return;
    setProfileSaving(true);
    setSaveMessage(null);
    try {
      await apiFetch(`/profile/${profile.basics?.profile_id || "asha_demo_001"}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data: profile }),
      });
      setSaveMessage("saved");
    } catch (err) {
      console.error("Save failed:", err);
      setSaveMessage("error");
    } finally {
      setProfileSaving(false);
      setTimeout(() => setSaveMessage(null), 3000);
    }
  };

  useEffect(() => {
    fetchData();
    fetchAlerts();
    fetchSuggestions();
    fetchProfile();

    const unsubscribeEvents = subscribeEvents((msg) => {
      if (msg.type === "status") {
        try {
          const data = msg.data as EscalationStatus;
          setStatus(data);
          if (data?.tier) setTier(data.tier);
        } catch {}
        return;
      }
      if (msg.type === "alert") {
        try {
          const data = msg.data as Alert;
          setAlerts((prev) => [data, ...prev].slice(0, 10));
          setEvents((prev) => [{ ...data, type: "alert" }, ...prev].slice(0, 40));
        } catch {}
        return;
      }
      if (msg.type === "events") {
        try {
          const arr = msg.data as Alert[];
          setEvents((prev) =>
            [...arr.reverse().map((a) => ({ ...a, type: "alert" } as Event)), ...prev].slice(0, 40)
          );
        } catch {}
        return;
      }
      if (msg.type === "event") {
        const inner = msg.data as { type?: string; [key: string]: unknown };
        const handler = handlers[inner.type ?? ""];
        if (handler) {
          try { handler({ data: JSON.stringify(inner) } as unknown as MessageEvent); } catch {}
        }
        return;
      }
    });

    const handlers: Record<string, (e: MessageEvent) => void> = {
      status: (e) => { try { setStatus(JSON.parse(e.data)); } catch {} },
      tier_change: (e) => {
        try {
          const data = JSON.parse(e.data);
          setEvents((prev) => [{ ...data, type: "tier_change" }, ...prev].slice(0, 40));
          const newTier = data.detail.new_tier as EscalationTier;
          setStatus((prev) => prev ? { ...prev, tier: newTier } : null);
          setTier(newTier);
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
          if (data.detail?.speaker === "sahaara") fetchWhyContext();
        } catch {}
      },
      memory_box_played: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "memory_box_played" }, ...prev].slice(0, 40)); } catch {} },
      sahaara_initiative: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "sahaara_initiative" }, ...prev].slice(0, 40)); } catch {} },
      alert_sent: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "alert_sent" }, ...prev].slice(0, 40)); } catch {} },
      action: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "action" }, ...prev].slice(0, 40)); } catch {} },
      reminder_fired: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "reminder_fired" }, ...prev].slice(0, 40)); } catch {} },
    };

    const unsubscribeConnection = subscribeConnection((live) => {
      if (live) fetchData();
    });

    return () => {
      unsubscribeEvents();
      unsubscribeConnection();
    };
  }, [setTier, fetchData, fetchWhyContext]);

  useEffect(() => {
    fetchSuggestions();
    const interval = setInterval(fetchSuggestions, 30000);
    return () => clearInterval(interval);
  }, [fetchSuggestions]);

  useEffect(() => {
    fetchDailySummary();
  }, [fetchDailySummary]);

  const handleAcknowledge = async (alert: Alert) => {
    try {
      console.log("Acknowledging alert:", alert.alert_id);
      const res = await apiFetch(`/alerts/${alert.alert_id}/acknowledge`, { method: "POST" });
      console.log("Acknowledge response:", res.status, await res.json());
      fetchAlerts();
      fetchSuggestions();
    } catch (err) {
      console.error("Failed to acknowledge alert:", err);
    }
  };

  const handleApprove = (suggestion: Suggestion) => {
    setSuggestions((prev) => prev.filter((s) => s !== suggestion));
    setSuggestionsCount((c) => c - 1);
    fetchSuggestions();
  };

  const handleReject = (suggestion: Suggestion) => {
    setSuggestions((prev) => prev.filter((s) => s !== suggestion));
    setSuggestionsCount((c) => c - 1);
    fetchSuggestions();
  };

  const updateProfile = (section: string, key: string, value: any) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      if (next[section as keyof ProfileData] && typeof next[section as keyof ProfileData] === "object") {
        (next[section as keyof ProfileData] as Record<string, any>)[key] = value;
      }
      return next;
    });
  };

  const updateArrayProfile = (section: string, index: number, key: string, value: any) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      const arr = next[section as keyof ProfileData] as Array<Record<string, any>>;
      if (Array.isArray(arr) && arr[index]) {
        arr[index][key] = value;
      }
      return next;
    });
  };

  const toggleVisibility = (path: string) => {
    setVisibility((prev) => ({ ...prev, [path]: !prev[path] }));
  };

  if (!status) {
    return (
      <div className="dashboard-loading">
        <div className="loading-spinner" />
        <p>Loading...</p>
      </div>
    );
  }

  const recentEvents = events.slice(0, 10);

  return (
    <div className="dashboard-screen">
      <header className="dtop">
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="wordmark">Sahaara</div>
          <span className="muted">Asha's dashboard</span>
        </div>
        <nav className="tabs" role="tablist">
          <button
            className={`tab ${activeTab === "today" ? "active" : ""}`}
            role="tab"
            onClick={() => setActiveTab("today")}
            aria-selected={activeTab === "today"}
          >
            Today
          </button>
          <button
            className={`tab ${activeTab === "alerts" ? "active" : ""}`}
            role="tab"
            onClick={() => setActiveTab("alerts")}
            aria-selected={activeTab === "alerts"}
          >
            Alerts {alerts.length > 0 && <span className="badge">{alerts.length}</span>}
          </button>
          <button
            className={`tab ${activeTab === "profile" ? "active" : ""}`}
            role="tab"
            onClick={() => setActiveTab("profile")}
            aria-selected={activeTab === "profile"}
          >
            Profile {suggestionsCount > 0 && <span className="badge">{suggestionsCount}</span>}
          </button>
        </nav>
        <div className="header-actions">
          <button className="btn ghost sm" onClick={() => navigateTo("/choose")}>
            Switch mode
          </button>
          <button className="btn ghost sm" onClick={logout}>
            Logout
          </button>
        </div>
      </header>

      <main className="dmain">
        {activeTab === "today" && (
          <div className="pane on" id="p-today">
            <div className="stats">
              <StatusCard label="Tier" value={formatDuration(status.tier_duration_seconds)} color={["var(--sage)", "var(--amber)", "var(--orange)", "var(--red)"][TIER_ORDER.indexOf(status.tier)]} />
              <StatusCard label="Repeats" value={`${status.last_repeat_count} / ${status.repeat_threshold}`} />
              <StatusCard label="Conversations" value={String(events.filter(e => e.type === "conversation_turn" && e.detail.speaker === "sahaara").length)} />
            </div>
            <div className="card" style={{ marginBottom: "1rem" }}>
              <small className="muted">ESCALATION LADDER</small>
              <div className="ladder" id="lad">
                {TIER_ORDER.map((tier) => (
                  <TierRow key={tier} tier={tier} isActive={status.tier === tier} />
                ))}
              </div>
            </div>
            <div className="grid2">
              <div className="card">
                <h3>Today's summary</h3>
                <p className="muted" style={{ marginTop: "0.5rem", whiteSpace: "pre-wrap" }}>
                  {dailySummary || "Loading..."}
                </p>
                <p className="note" style={{ marginTop: "0.9rem" }}>
                  Noise reduction by design, not a clinical measure.
                </p>
              </div>
              <div className="card">
                <h3>Conversations</h3>
                <div className="conversation-scroll">
                  <ul className="tl">
                    {recentEvents.length === 0 ? (
                      <li style={{ padding: "1rem", color: "var(--muted)" }}>No events yet</li>
                    ) : (
                      recentEvents.map((event, idx) => (
                        <EventItem key={idx} event={event} />
                      ))
                    )}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "alerts" && (
          <div className="pane on" id="p-alerts">
            {alerts.length === 0 ? (
              <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
                <p className="muted">No alerts</p>
              </div>
            ) : (
              alerts.map((alert) => (
                <AlertCard
                  key={alert.alert_id}
                  alert={alert}
                  onAcknowledge={() => handleAcknowledge(alert)}
                />
              ))
            )}
          </div>
        )}

        {activeTab === "profile" && (
          <div className="pane on" id="p-profile">
            {suggestions.length > 0 && (
              <div className="card review" id="rev">
                <h3>Sahaara noticed {suggestions.length} thing{suggestions.length !== 1 ? "s" : ""}</h3>
                <p className="note">Nothing is added to Asha's profile until you approve it.</p>
                <div id="rrows">
                  {suggestions.map((suggestion) => (
                    <SuggestionCard
                      key={suggestion.suggestion_id}
                      suggestion={suggestion}
                      onApprove={() => handleApprove(suggestion)}
                      onReject={() => handleReject(suggestion)}
                    />
                  ))}
                </div>
              </div>
            )}
            {suggestions.length === 0 && (
              <div className="card" id="rev" style={{ marginBottom: "1rem" }}>
                <p className="muted">Nothing to review right now.</p>
                <p className="note">While chatting, Sahaara notices things about Asha (a song she likes, a memory she shared). Nothing is added to her profile until you approve it.</p>
              </div>
            )}

            {PROFILE_SECTIONS.map((section: SectionConfig) => (
              <div className="card sec" key={section.id} style={{ marginBottom: "1rem" }}>
                <h3>{section.title}</h3>
                {section.note && <p className="note">{section.note}</p>}
                {section.id === "family" && profile?.family && (
                  <>
                    {profile.family.map((member, idx) => (
                      <div className="fr" key={idx}>
                        <div style={{ display: "flex", gap: "0.5rem", flex: 1 }}>
                          <input
                            value={member.name || ""}
                            onChange={(e) => updateArrayProfile("family", idx, "name", e.target.value)}
                            placeholder="Name"
                            style={{ flex: 1 }}
                          />
                          <input
                            value={member.relationship || ""}
                            onChange={(e) => updateArrayProfile("family", idx, "relationship", e.target.value)}
                            placeholder="Relationship"
                            style={{ width: "110px" }}
                          />
                          <select
                            value={member.living_status || "living"}
                            onChange={(e) => updateArrayProfile("family", idx, "living_status", e.target.value)}
                            style={{ width: "100px" }}
                          >
                            <option value="living">Living</option>
                            <option value="late">Late</option>
                          </select>
                          <input
                            value={member.phone || ""}
                            onChange={(e) => updateArrayProfile("family", idx, "phone", e.target.value)}
                            placeholder="Phone"
                            style={{ flex: 1 }}
                          />
                          <button
                            className="vis"
                            style={{ background: visibility[`family.${idx}.phone`] ? "color-mix(in srgb, var(--sage) 18%, var(--surface))" : "var(--surface2)", color: visibility[`family.${idx}.phone`] ? "var(--sage)" : "var(--muted)", borderColor: visibility[`family.${idx}.phone`] ? "var(--sage)" : "var(--line)" }}
                            onClick={() => toggleVisibility(`family.${idx}.phone`)}
                          >
                            {visibility[`family.${idx}.phone`] ? "Shared with Sahaara" : "Caregiver only"}
                          </button>
                        </div>
                        <button className="btn sm ghost" onClick={() => setProfile((p) => p ? { ...p, family: p.family.filter((_, i) => i !== idx) } : p)}>✕</button>
                      </div>
                    ))}
                    <button className="btn ghost sm" onClick={() => setProfile((p) => p ? { ...p, family: [...p.family, { name: "", relationship: "", living_status: "living", phone: "", may_offer_notify: true, notify_order: null, notes: "", memory_box_clip: null }] } : p)}>+ Add family member</button>
                  </>
                )}
                {section.id === "avoid" && profile?.avoid && (
                  <>
                    {profile.avoid.topics?.map((topic: any, idx: number) => (
                      <div className="rrow" key={idx} style={{ flexDirection: "column", gap: "0.5rem", alignItems: "flex-start" }}>
                        <div style={{ display: "flex", gap: "0.5rem", width: "100%" }}>
                          <input
                            value={topic.topic || ""}
                            onChange={(e) => updateArrayProfile("avoid.topics", idx, "topic", e.target.value)}
                            placeholder="Topic (e.g. Vikram's death)"
                            style={{ flex: 2 }}
                          />
                          <select
                            value={topic.setting || "avoid"}
                            onChange={(e) => updateArrayProfile("avoid.topics", idx, "setting", e.target.value)}
                            style={{ width: "150px" }}
                          >
                            <option value="avoid">Avoid</option>
                            <option value="redirect">Redirect</option>
                            <option value="allow_with_care">Allow with care</option>
                          </select>
                          <button className="btn sm ghost" onClick={() => setProfile((p) => p ? { ...p, avoid: { ...p.avoid, topics: p.avoid.topics.filter((_: any, i: number) => i !== idx) } } : p)}>✕</button>
                        </div>
                        {topic.setting === "redirect" && (
                          <input
                            value={topic.redirect_to || ""}
                            onChange={(e) => updateArrayProfile("avoid.topics", idx, "redirect_to", e.target.value)}
                            placeholder="e.g. happy memories of teaching"
                            style={{ width: "100%" }}
                          />
                        )}
                        <input
                          value={topic.notes || ""}
                          onChange={(e) => updateArrayProfile("avoid.topics", idx, "notes", e.target.value)}
                          placeholder="Why this setting?"
                          style={{ width: "100%" }}
                        />
                      </div>
                    ))}
                    <button className="btn ghost sm" onClick={() => setProfile((p) => p ? { ...p, avoid: { ...p.avoid, topics: [...(p.avoid.topics || []), { topic: "", setting: "avoid", redirect_to: "", notes: "" }] } } : p)}>+ Add topic</button>
                    <div className="fr">
                      <label className="field">
                        <span>If she asks for someone who has died</span>
                        <input
                          value={profile.avoid.when_asking_for_deceased || ""}
                          onChange={(e) => updateProfile("avoid", "when_asking_for_deceased", e.target.value)}
                          placeholder="What to say when she asks for someone who has died"
                        />
                      </label>
                      <button
                        className="vis"
                        style={{ background: visibility["avoid.when_asking_for_deceased"] ? "color-mix(in srgb, var(--sage) 18%, var(--surface))" : "var(--surface2)", color: visibility["avoid.when_asking_for_deceased"] ? "var(--sage)" : "var(--muted)", borderColor: visibility["avoid.when_asking_for_deceased"] ? "var(--sage)" : "var(--line)" }}
                        onClick={() => toggleVisibility("avoid.when_asking_for_deceased")}
                      >
                        {visibility["avoid.when_asking_for_deceased"] ? "Shared with Sahaara" : "Caregiver only"}
                      </button>
                    </div>
                  </>
                )}
                {["basics", "life_story", "likes", "daily_routine", "health_notes", "communication_tips"].includes(section.id) && (
                  <>
                    {section.fields.map((field) => (
                      <div className="fr" key={field.key}>
                        <label className="field">
                          <span>{field.label}</span>
                          <input
                            value={profile?.[section.id]?.[field.key] || ""}
                            onChange={(e) => updateProfile(section.id, field.key, e.target.value)}
                            placeholder="Add details"
                          />
                        </label>
                        <button
                          className="vis"
                          style={{ background: visibility[`${section.id}.${field.key}`] ? "color-mix(in srgb, var(--sage) 18%, var(--surface))" : "var(--surface2)", color: visibility[`${section.id}.${field.key}`] ? "var(--sage)" : "var(--muted)", borderColor: visibility[`${section.id}.${field.key}`] ? "var(--sage)" : "var(--line)" }}
                          onClick={() => toggleVisibility(`${section.id}.${field.key}`)}
                        >
                          {visibility[`${section.id}.${field.key}`] ? "Shared with Sahaara" : "Caregiver only"}
                        </button>
                      </div>
                    ))}
                  </>
                )}
                {section.id === "health_notes" && section.note && <p className="note" style={{ marginTop: "0.5rem" }}>{section.note}</p>}
              </div>
            ))}

            <button className="btn" onClick={saveProfile} disabled={profileSaving}>
              {profileSaving ? "Saving..." : saveMessage === "saved" ? "✓ Saved" : "Save profile"}
            </button>
          </div>
        )}
      </main>

      {whyExplanation && (
        <div className="why-panel-minimal">
          <h4 className="why-panel__title">Why did Sahaara say this?</h4>
          <p className="why-panel__text">{whyExplanation}</p>
        </div>
      )}
    </div>
  );
};