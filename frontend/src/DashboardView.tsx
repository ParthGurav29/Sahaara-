import { useEffect, useState, useCallback, useRef } from "react";
import { apiFetch } from "./api";
import { subscribeEvents, subscribeConnection } from "./eventStream";
import { describePushAttempt } from "./pushStatus";
import { describeCallStatus } from "./callStatus";
import { useVoiceActions } from "./VoiceStateContext";
import { useAuth } from "./AuthContext";
import { navigateTo } from "./useRoute";
import PushButton from "./PushButton";
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

interface EmergencyIncident {
  id: string;
  profile_id: string;
  trigger: string;
  trigger_phrase: string | null;
  tier: string;
  simulated: boolean;
  provider: string;
  status: string;
  reason: string;
  gate_reason?: string;
  attempts: number;
  started_at: number;
  ended_at: number | null;
  attempts_detail?: EmergencyAttempt[];
}

interface EmergencyAttempt {
  id: string;
  incident_id: string;
  contact_order: number;
  contact_name: string;
  contact_rel: string;
  phone_masked: string;
  attempt: number;
  status: string;
  provider_call_id: string | null;
  simulated: boolean;
  started_at: number;
  answered_at: number | null;
  acknowledged_at: number | null;
  ended_at: number | null;
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
  emergency_call_started: "URGENT CALL",
  emergency_call_attempt: "CALL ATTEMPT",
  emergency_call_status: "CALL STATUS",
  emergency_call_ended: "CALL ENDED",
  emergency_call_skipped: "CALL SKIPPED",
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
  emergency_call_started: "#fdf0e6",
  emergency_call_attempt: "#fdf3ea",
  emergency_call_status: "#f7f4ef",
  emergency_call_ended: "#eef2f0",
  emergency_call_skipped: "#f3f1ec",
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
  emergency_call_started: "#8a4b16",
  emergency_call_attempt: "#8a4b16",
  emergency_call_status: "#5c5650",
  emergency_call_ended: "#3f5d4e",
  emergency_call_skipped: "#6b6660",
};

const FIELD_LABELS: Record<string, string> = {
  "likes.music": "Music they like",
  "likes.topics": "Topics they enjoy",
  "likes.food": "Food/drink they like",
  key_events: "Life story event",
  daily_routine: "Daily routine",
  family: "Person mentioned",
};

// Plain-language reasons for an emergency campaign ending or being skipped.
// These describe what the call provider reported — never what a person did or
// understood ("acknowledged" means a keypress reached the provider).
const CALL_END_REASONS: Record<string, string> = {
  acknowledged: "acknowledged on the call",
  exhausted: "no contact acknowledged a call",
  failed: "the call could not be placed",
  simulated: "no call was placed",
  triggered: "still running",
  all_contacts_exhausted: "no contact acknowledged a call",
  max_attempts_reached: "every allowed attempt was used",
  max_incident_time: "the time limit for this call-out was reached",
  cancelled: "stopped by the caregiver",
  canceled: "stopped by the caregiver",
  caregiver_cancelled: "stopped by the caregiver",
  cancelled_by_caregiver: "stopped by the caregiver", // legacy spelling, pre-Task-4 rows
  disabled: "calling is switched off",
  no_callable_contact: "no contact has both a number and permission to be called",
  cooldown: "a recent emergency call was already placed",
  orchestrator_error: "the call could not be placed",
};

const CALL_SKIP_REASONS: Record<string, string> = {
  cooldown: "a recent emergency call was already placed",
  no_callable_contact: "no contact has both a number and permission to be called",
  disabled: "calling is switched off",
  already_active: "a call-out was already running",
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
    case "emergency_call_started":
      return `Urgent symptom — contacting caregivers${d.simulated ? " · simulated" : ""}`;
    case "emergency_call_attempt":
      return `Calling ${d.contact_name} (${d.phone_masked}) — try ${d.attempt}${d.simulated ? " · simulated" : ""}`;
    case "emergency_call_status": {
      // `describeCallStatus` already appends "· simulated" — do not add it twice.
      const desc = describeCallStatus({ status: String(d.status ?? ""), simulated: Boolean(d.simulated) });
      return desc?.label ?? String(d.status ?? "status");
    }
    case "emergency_call_ended": {
      const reason = CALL_END_REASONS[String(d.reason ?? "")] ?? `${d.status ?? "ended"}`;
      return `Ended — ${reason}${d.simulated ? " · simulated" : ""}`;
    }
    case "emergency_call_skipped": {
      const reason = CALL_SKIP_REASONS[String(d.reason ?? "")] ?? "not placed";
      return `No call placed — ${reason}`;
    }
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

function EmergencyIncidentCard({ incident, onCancel }: { incident: EmergencyIncident; onCancel: () => void }) {
  const tierColors: Record<string, string> = {
    green: "var(--sage)",
    yellow: "var(--amber)",
    orange: "var(--orange)",
    red: "var(--red)",
  };
  const tierColor = tierColors[incident.tier] || "var(--ink)";

  const getAttemptDescription = (attempt: EmergencyAttempt) => {
    const callStatus = {
      status: attempt.status,
      simulated: incident.simulated,
      contact_name: attempt.contact_name,
      phone_masked: attempt.phone_masked,
      attempt: attempt.attempt,
    };
    const desc = describeCallStatus(callStatus);
    if (desc) return desc.label;
    return attempt.status;
  };

  const isActive = incident.status === "triggered" || incident.status === "dialing" || 
                   incident.status === "ringing" || incident.status === "answered" || 
                   incident.status === "answered_unacknowledged";

  return (
    <div className="card emergency-incident" style={{ borderLeftColor: tierColor } as React.CSSProperties}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
        <div>
          <h3 style={{ color: tierColor }}>
            EMERGENCY CALL · {incident.tier.toUpperCase()}
          </h3>
          <p className="muted" style={{ fontSize: "0.85rem" }}>
            {incident.trigger === "physical_urgent" && incident.trigger_phrase
              ? `Physical urgent: "${incident.trigger_phrase}"`
              : incident.trigger === "danger"
              ? "Danger statement"
              : incident.trigger === "consent_accepted"
              ? "Consent accepted"
              : incident.trigger}
            {incident.simulated && <span className="simulated-badge">SIMULATED</span>}
          </p>
        </div>
        {isActive && (
          <button className="btn sm ghost" onClick={onCancel}>I'm on it</button>
        )}
      </div>

      <div style={{ marginBottom: "0.5rem" }}>
        <small className="muted">
          {formatDate(new Date(incident.started_at * 1000).toISOString())} · {formatTime(new Date(incident.started_at * 1000).toISOString())}
        </small>
      </div>

      <div style={{ borderTop: "1px solid var(--line)", paddingTop: "0.5rem" }}>
        {incident.attempts_detail?.map((attempt) => (
          <div key={attempt.id} className="attempt-row" style={{ marginBottom: "0.25rem", paddingLeft: "1rem", borderLeft: "2px solid var(--line)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
              <span>
                Attempt {attempt.attempt} · {attempt.contact_name} {attempt.contact_rel && `(${attempt.contact_rel})`} · {attempt.phone_masked}
              </span>
              <span className="attempt-status">
                {getAttemptDescription(attempt)}
              </span>
            </div>
            {attempt.answered_at && (
              <small className="muted">Answered at {formatTime(new Date(attempt.answered_at * 1000).toISOString())}</small>
            )}
            {attempt.acknowledged_at && (
              <small style={{ color: "var(--sage)" }}>Acknowledged at {formatTime(new Date(attempt.acknowledged_at * 1000).toISOString())}</small>
            )}
          </div>
        ))}
        {(incident.attempts_detail?.length ?? 0) === 0 && incident.status === "triggered" && (
          <small className="muted">Starting the call…</small>
        )}
      </div>

      {incident.ended_at && (
        <div style={{ marginTop: "0.5rem", paddingTop: "0.5rem", borderTop: "1px solid var(--line)" }}>
          <small className="muted">
            Ended: {CALL_END_REASONS[incident.status] ?? incident.status}
            {incident.reason &&
              // Compare the *phrased* text, not the raw keys: `cancelled` and
              // `caregiver_cancelled` are different keys with the same sentence.
              (CALL_END_REASONS[incident.reason] ?? incident.reason) !==
                (CALL_END_REASONS[incident.status] ?? incident.status) &&
              ` · ${CALL_END_REASONS[incident.reason] ?? incident.reason}`}
          </small>
        </div>
      )}

      {incident.simulated && (
        <div className="simulated-banner" style={{ marginTop: "0.75rem", padding: "0.5rem", background: "var(--surface2)", border: "1px solid var(--line)", borderRadius: "4px", fontSize: "0.8rem", color: "var(--muted)" }}>
          ⚠ SIMULATED — {incident.gate_reason ? incident.gate_reason : "no real call was placed"}
        </div>
      )}
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

type FieldType = "text" | "textarea" | "array";

// The Profile tab edits the profile document directly — one section per real key
// in asha_profile.json. No invented "basics"/"health_notes"/"communication_tips"
// fields that have nowhere to persist. `type: "array"` renders a comma-separated
// input and splits it back into a list on save.
const PROFILE_SECTIONS: Array<{
  id: string;
  title: string;
  note?: string;
  fields: Array<{ path: string[]; label: string; type: FieldType; shared: boolean }>;
}> = [
  {
    id: "basics",
    title: "Basics",
    fields: [
      { path: ["name"], label: "Name", type: "text", shared: true },
      { path: ["life_story"], label: "Life story", type: "textarea", shared: true },
    ],
  },
  {
    id: "likes",
    title: "Likes and what soothes her",
    fields: [
      { path: ["likes", "music"], label: "Music", type: "array", shared: true },
      { path: ["likes", "food"], label: "Food and drink", type: "array", shared: true },
      { path: ["likes", "topics"], label: "Topics she enjoys", type: "array", shared: true },
      { path: ["likes", "routines"], label: "Routines", type: "array", shared: true },
    ],
  },
  {
    id: "avoid",
    title: "Sensitive topics",
    fields: [
      { path: ["avoid", "topics"], label: "Topics to avoid or redirect", type: "array", shared: true },
    ],
  },
  {
    id: "daily_routine",
    title: "Daily routine",
    fields: [
      { path: ["daily_routine", "wake_time"], label: "Wake time", type: "text", shared: true },
      { path: ["daily_routine", "breakfast"], label: "Breakfast", type: "text", shared: true },
      { path: ["daily_routine", "lunch"], label: "Lunch", type: "text", shared: true },
      { path: ["daily_routine", "tea_time"], label: "Tea time", type: "text", shared: true },
      { path: ["daily_routine", "dinner"], label: "Dinner", type: "text", shared: true },
      { path: ["daily_routine", "sleep_time"], label: "Sleep time", type: "text", shared: true },
      { path: ["daily_routine", "restless_window"], label: "Restless window", type: "text", shared: true },
      { path: ["daily_routine", "medication_times"], label: "Medication times (reminders only)", type: "array", shared: false },
    ],
  },
];

// The profile document is the source of truth (asha_profile.json). Loose on
// purpose: the form reads/writes the real keys directly through `path` arrays.
type ProfileData = Record<string, any>;

interface SectionConfig {
  id: string;
  title: string;
  note?: string;
  fields: Array<{ path: string[]; label: string; type: FieldType; shared: boolean }>;
}

export default function DashboardView() {
  const { setTier } = useVoiceActions();
  const { logout } = useAuth();
  const [status, setStatus] = useState<EscalationStatus | null>(null);
  const [loadError, setLoadError] = useState(false);
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
  // Emergency Call incidents
  const [emergencyIncidents, setEmergencyIncidents] = useState<EmergencyIncident[]>([]);
  // Connection state for the shared event socket
  const [socketConnected, setSocketConnected] = useState(false);
  // Debounce timer for refetches triggered by events
  const refetchDebounceTimer = useRef<number | null>(null);
  // Polling timer for slow fallback (20s, paused when hidden)
  const pollingTimer = useRef<number | null>(null);
  // Track if the tab is currently visible
  const isTabVisible = useRef(true);

  const fetchWhyContext = useCallback(async () => {
    try {
      const res = await apiFetch("/turn/context");
      const data = await res.json();
      if (data.explanation) setWhyExplanation(data.explanation);
    } catch {}
  }, []);

  const fetchData = useCallback(async () => {
    // The tier is what the whole screen is built from, so a failure here is a
    // real "cannot render" state — never an endless spinner. Log it, show an
    // honest retry, and let everything else degrade quietly.
    try {
      const statusRes = await apiFetch("/escalation/status");
      const statusData = await statusRes.json();
      setStatus(statusData);
      if (statusData?.tier) setTier(statusData.tier as EscalationTier);
      setLoadError(false);
    } catch (err) {
      console.error("Failed to fetch escalation status:", err);
      setLoadError(true);
      return;
    }

    try {
      const eventsRes = await apiFetch("/escalation/events?limit=20");
      const eventsData = await eventsRes.json();
      setEvents(Array.isArray(eventsData.events) ? [...eventsData.events].reverse() : []);
    } catch (err) {
      console.error("Failed to fetch escalation events:", err);
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

  const fetchEmergencyIncidents = useCallback(async () => {
    try {
      const res = await apiFetch("/emergency/incidents?limit=20");
      const data = await res.json();
      if (data.incidents) {
        setEmergencyIncidents(data.incidents);
      }
    } catch (err) {
      console.error("Failed to fetch emergency incidents:", err);
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
      await apiFetch(`/profile/${profile.profile_id || "asha_demo_001"}`, {
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

  // Debounced refetch helper — coalesces multiple rapid events into one refetch
  const scheduleRefetch = useCallback((fn: () => void) => {
    if (refetchDebounceTimer.current !== null) {
      window.clearTimeout(refetchDebounceTimer.current);
    }
    refetchDebounceTimer.current = window.setTimeout(() => {
      if (isTabVisible.current) {
        fn();
      }
    }, 500);
  }, []);

  // Slow polling fallback (20s, paused when tab is hidden)
  useEffect(() => {
    const poll = () => {
      if (isTabVisible.current) {
        fetchData();
        fetchAlerts();
        fetchEmergencyIncidents();
        fetchSuggestions();
      }
    };
    pollingTimer.current = window.setInterval(poll, 20000);
    return () => {
      if (pollingTimer.current !== null) {
        window.clearInterval(pollingTimer.current);
        pollingTimer.current = null;
      }
    };
  }, [fetchData, fetchAlerts, fetchEmergencyIncidents, fetchSuggestions]);

  // Visibility change handler — refetch when tab becomes visible
  useEffect(() => {
    const handleVisibilityChange = () => {
      isTabVisible.current = !document.hidden;
      if (!document.hidden) {
        // Refetch everything when tab becomes visible (missed frames while asleep)
        fetchData();
        fetchAlerts();
        fetchEmergencyIncidents();
        fetchSuggestions();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [fetchData, fetchAlerts, fetchEmergencyIncidents, fetchSuggestions]);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (refetchDebounceTimer.current !== null) {
        window.clearTimeout(refetchDebounceTimer.current);
        refetchDebounceTimer.current = null;
      }
    };
  }, []);

  useEffect(() => {
    fetchData();
    fetchAlerts();
    fetchEmergencyIncidents();
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
          scheduleRefetch(fetchAlerts);
          scheduleRefetch(fetchSuggestions);
        } catch {}
        return;
      }
      if (msg.type === "events") {
        try {
          const arr = msg.data as Alert[];
          setEvents((prev) =>
            [...arr.reverse().map((a) => ({ ...a, type: "alert" } as Event)), ...prev].slice(0, 40)
          );
          scheduleRefetch(fetchAlerts);
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
          scheduleRefetch(fetchAlerts);
          scheduleRefetch(fetchSuggestions);
        } catch {}
      },
      consent_asked: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "consent_asked" }, ...prev].slice(0, 40)); scheduleRefetch(fetchSuggestions); } catch {} },
      consent_resolved: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "consent_resolved" }, ...prev].slice(0, 40)); scheduleRefetch(fetchSuggestions); } catch {} },
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
      alert_sent: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "alert_sent" }, ...prev].slice(0, 40)); scheduleRefetch(fetchAlerts); } catch {} },
      action: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "action" }, ...prev].slice(0, 40)); } catch {} },
      reminder_fired: (e) => { try { setEvents((prev) => [{ ...JSON.parse(e.data), type: "reminder_fired" }, ...prev].slice(0, 40)); } catch {} },
      // Emergency Call events. The event envelope is
      // {type, timestamp, profile_id, detail}; we add it to the Day Timeline
      // and refetch the incident list so the attempt rows come from the
      // database rather than being reconstructed from partial frames.
      emergency_call_started: (e) => { try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "emergency_call_started" }, ...prev].slice(0, 40));
        fetchEmergencyIncidents();
      } catch {} },
      emergency_call_attempt: (e) => { try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "emergency_call_attempt" }, ...prev].slice(0, 40));
        fetchEmergencyIncidents();
      } catch {} },
      emergency_call_status: (e) => { try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "emergency_call_status" }, ...prev].slice(0, 40));
        fetchEmergencyIncidents();
      } catch {} },
      emergency_call_ended: (e) => { try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "emergency_call_ended" }, ...prev].slice(0, 40));
        fetchEmergencyIncidents();
      } catch {} },
      emergency_call_skipped: (e) => { try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [{ ...data, type: "emergency_call_skipped" }, ...prev].slice(0, 40));
        fetchEmergencyIncidents();
      } catch {} },
    };

    const unsubscribeConnection = subscribeConnection((live) => {
      setSocketConnected(live);
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

  const handleCancelEmergencyIncident = async (incidentId: string) => {
    try {
      console.log("Cancelling emergency incident:", incidentId);
      const res = await apiFetch(`/emergency/incidents/${incidentId}/cancel`, { method: "POST" });
      console.log("Cancel response:", res.status, await res.json());
      fetchEmergencyIncidents();
    } catch (err) {
      console.error("Failed to cancel emergency incident:", err);
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

  const readProfilePath = (path: string[]): string => {
    let node: unknown = profile;
    for (const key of path) {
      if (node && typeof node === "object") node = (node as Record<string, unknown>)[key];
      else return "";
    }
    if (node == null) return "";
    return Array.isArray(node) ? node.join(", ") : String(node);
  };

  const writeProfilePath = (path: string[], value: unknown) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      let node: Record<string, any> = next;
      for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        if (node[key] === null || typeof node[key] !== "object" || Array.isArray(node[key])) {
          node[key] = {};
        }
        node = node[key];
      }
      node[path[path.length - 1]] = value;
      return next;
    });
  };

  const updateProfileField = (field: SectionConfig["fields"][number], text: string) => {
    if (field.type === "array") {
      const list = text.split(",").map((s) => s.trim()).filter(Boolean);
      writeProfilePath(field.path, list);
    } else {
      writeProfilePath(field.path, text);
    }
  };

  const updateFamilyMember = (index: number, key: string, value: unknown) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      if (!Array.isArray(next.family)) next.family = [];
      if (!next.family[index]) next.family[index] = {};
      next.family[index][key] = value;
      return next;
    });
  };

  const addFamilyMember = () => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      if (!Array.isArray(next.family)) next.family = [];
      next.family.push({
        name: "",
        relationship: "",
        phone: null,
        notes: "",
        notify_order: null,
        may_offer_notify: false,
        may_call: false,
      });
      return next;
    });
  };

  const removeFamilyMember = (index: number) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      if (Array.isArray(next.family)) next.family = next.family.filter((_: unknown, i: number) => i !== index);
      return next;
    });
  };

  const updateRedirect = (topic: string, value: string) => {
    setProfile((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev));
      if (next.avoid === null || typeof next.avoid !== "object" || Array.isArray(next.avoid)) next.avoid = {};
      if (next.avoid.redirects === null || typeof next.avoid.redirects !== "object" || Array.isArray(next.avoid.redirects)) next.avoid.redirects = {};
      next.avoid.redirects[topic] = value;
      return next;
    });
  };

  if (!status) {
    if (loadError) {
      return (
        <div className="dashboard-loading">
          <p>Couldn't load the dashboard.</p>
          <p className="muted">The server didn't answer. Nothing is lost — try again.</p>
          <button
            className="btn"
            type="button"
            onClick={() => {
              setLoadError(false);
              void fetchData();
              void fetchAlerts();
              void fetchSuggestions();
              void fetchProfile();
            }}
          >
            Try again
          </button>
        </div>
      );
    }
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
        <div className="dtop-brand">
          <div className="wordmark">Sahaara</div>
          <span className="dtop-subtitle">Asha's dashboard</span>
          <span className={`connection-indicator ${socketConnected ? "connected" : "reconnecting"}`}>
            {socketConnected ? "Live" : "Reconnecting…"}
          </span>
        </div>
        <div className="header-actions">
          <PushButton />
          <button className="btn ghost sm" onClick={() => navigateTo("/choose")}>
            Switch mode
          </button>
          <button className="btn ghost sm" onClick={logout}>
            Logout
          </button>
        </div>
      </header>

      <nav className="dtabs" role="tablist">
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
          Alerts {(alerts.length + emergencyIncidents.length) > 0 && <span className="badge">{alerts.length + emergencyIncidents.length}</span>}
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
            {emergencyIncidents.length === 0 && alerts.length === 0 ? (
              <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
                <p className="muted">No alerts or emergency calls</p>
              </div>
            ) : (
              <>
                {emergencyIncidents.length > 0 && (
                  <div style={{ marginBottom: "1rem" }}>
                    <h3 style={{ marginBottom: "0.5rem" }}>Emergency Calls</h3>
                    {emergencyIncidents.map((incident) => (
                      <EmergencyIncidentCard
                        key={incident.id}
                        incident={incident}
                        onCancel={() => handleCancelEmergencyIncident(incident.id)}
                      />
                    ))}
                  </div>
                )}
                {alerts.length === 0 && emergencyIncidents.length > 0 ? null : (
                  alerts.length === 0 ? (
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
                  )
                )}
              </>
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

            {/* Family & contacts */}
            <div className="card sec" style={{ marginBottom: "1rem" }}>
              <h3>Family and contacts</h3>
              {(profile?.family ?? []).map((member: any, idx: number) => (
                <div className="fr family" key={idx}>
                  <div className="family-fields">
                    <input className="ff-name" placeholder="Name" value={member.name || ""} onChange={(e) => updateFamilyMember(idx, "name", e.target.value)} />
                    <input className="ff-rel" placeholder="Relationship" value={member.relationship || ""} onChange={(e) => updateFamilyMember(idx, "relationship", e.target.value)} />
                    <input className="ff-phone" placeholder="Phone" value={member.phone || ""} onChange={(e) => updateFamilyMember(idx, "phone", e.target.value)} />
                    <input className="ff-rel" placeholder="Notify order" value={member.notify_order ?? ""} onChange={(e) => updateFamilyMember(idx, "notify_order", e.target.value === "" ? null : Number(e.target.value))} />
                  </div>
                  <button className="btn sm ghost family-remove" onClick={() => removeFamilyMember(idx)}>✕</button>
                </div>
              ))}
              <button className="btn ghost sm" onClick={addFamilyMember}>+ Add family member</button>
            </div>

            {/* Sensitive-topic replies */}
            {profile?.avoid?.redirects && (
              <div className="card sec" style={{ marginBottom: "1rem" }}>
                <h3>What Sahaara says about sensitive topics</h3>
                {Object.entries(profile.avoid.redirects).map(([topic, reply]) => (
                  <label className="field" key={topic}>
                    <span>When she asks about "{topic}"</span>
                    <input value={String(reply)} onChange={(e) => updateRedirect(topic, e.target.value)} placeholder="What Sahaara says" />
                  </label>
                ))}
              </div>
            )}

            {PROFILE_SECTIONS.map((section) => (
              <div className="card sec" key={section.id} style={{ marginBottom: "1rem" }}>
                <h3>{section.title}</h3>
                {section.note && <p className="note">{section.note}</p>}
                {section.fields.map((field) => (
                  <label className="field" key={field.path.join(".")}>
                    <span>{field.label}</span>
                    {field.type === "textarea" ? (
                      <textarea rows={3} value={readProfilePath(field.path)} onChange={(e) => updateProfileField(field, e.target.value)} placeholder="Add details" />
                    ) : (
                      <input value={readProfilePath(field.path)} onChange={(e) => updateProfileField(field, e.target.value)} placeholder={field.type === "array" ? "Comma separated" : "Add details"} />
                    )}
                  </label>
                ))}
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