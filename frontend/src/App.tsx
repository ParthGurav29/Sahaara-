import { Component, useEffect, useState } from "react";

import { AuthProvider, useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import { playMediaUrl, stopMediaUrl } from "./mediaPlayer";
import { unlockAlarmAudio } from "./alarm";
import LoginPage from "./LoginPage";
import { VoiceStateProvider } from "./VoiceStateContext";
import SahaaraAvatar from "./SahaaraAvatar";
import { useVoiceSession } from "./useVoiceSession";
import EscalationLadder from "./EscalationLadder";
import DailySummaryScreen from "./DailySummaryScreen";
import SuggestedFacts from "./SuggestedFacts";
import ProfileForm from "./ProfileForm";
import PushButton from "./PushButton";
import AvatarPreview from "./AvatarPreview";
import TaskAlarm from "./TaskAlarm";
import { parseAlertId, withoutAlertParam } from "./deepLink";
import "./App.css";

class ErrorBoundary extends Component<
  { children: React.ReactNode; name: string; fallback?: React.ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode; name: string; fallback?: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.name}]`, error, info.componentStack);
  }
  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="error-boundary-fallback">
          <p>Something went wrong in {this.props.name}.</p>
          <button onClick={() => this.setState({ hasError: false })}>Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

// The avatar replaced the orb completely (owner decision, 2026-10-28). It is
// the only voice visual and the only voice control: clicking it starts/stops
// the session exactly like the orb did, its mouth follows real TTS amplitude,
// and the escalation tier only ever calms it down.
//
// If the avatar's render ever throws, this plain control keeps the person able
// to talk — it is deliberately not the old orb.
function VoiceFallbackControl() {
  const { state, isActive, toggle } = useVoiceSession();

  return (
    <div className="voice-fallback">
      <p>Sahaara's animation had a problem.</p>
      <button type="button" onClick={toggle}>
        {isActive ? "Stop talking" : "Start talking"}
      </button>
      <p>{isActive ? state : "Voice is off"}</p>
    </div>
  );
}

function AshaVisual() {
  return (
    <section className="asha-view">
      <div className="asha-view-header">
        <h2 className="view-title">Asha's View</h2>
      </div>
      <ErrorBoundary name="SahaaraAvatar" fallback={<VoiceFallbackControl />}>
        <SahaaraAvatar />
      </ErrorBoundary>
    </section>
  );
}

function AppContent() {
  const { user, isLoading, authEnabled, logout } = useAuth();
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"live" | "summary" | "suggestions" | "profile">("live");
  // M5 Step 7: `/?alert=<id>` from a notification tap. Read once, then the
  // query is stripped so a refresh does not reopen the same alert.
  const [focusAlertId, setFocusAlertId] = useState<string | null>(() =>
    parseAlertId(window.location.search),
  );
  const [showChat, setShowChat] = useState(false);

  // Avatar A2: Dev preview access via `?avatar-preview=true`
  const [showAvatarPreview, setShowAvatarPreview] = useState(false);

  useEffect(() => {
    if (!focusAlertId) return;
    // The default view is already "live"; only the URL needs cleaning so a
    // refresh does not reopen the same alert.
    window.history.replaceState({}, "", withoutAlertParam(window.location.href));
  }, [focusAlertId]);

  // A timer has to ring even when nobody touches the screen at the moment it
  // fires, so unlock the audio context on the first real gesture. Browsers
  // refuse to start audio without one; doing it here means the chime is ready
  // long before the timer is due.
  useEffect(() => {
    const unlock = () => unlockAlarmAudio();
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  // A notification tap on an already-open app arrives here when the service
  // worker cannot reload the window (M5 Step 7).
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const onMessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || data.type !== "sahaara-open-alert") return;
      let search = "";
      try {
        search = new URL(data.url, window.location.origin).search;
      } catch {
        return;
      }
      const alertId = parseAlertId(search);
      if (alertId) setFocusAlertId(alertId);
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, []);

  // Dev-only: check for avatar preview flag
  useEffect(() => {
    if (import.meta.env.DEV) {
      const params = new URLSearchParams(window.location.search);
      if (params.get("avatar-preview") === "true") {
        setShowAvatarPreview(true);
        // Clean the URL
        params.delete("avatar-preview");
        window.history.replaceState({}, "", `${window.location.pathname}${params.toString() ? "?" + params.toString() : ""}`);
      }
    }
  }, []);

  // When auth is enabled, check if we should show caregiver tabs
  const showCaregiverTabs = !authEnabled || (user?.role === "caregiver");

  async function sendMessage() {
    if (!message.trim()) return;

    setLoading(true);
    setResponse("");

    try {
      const res = await apiFetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const data = await res.json();

      if (!res.ok || data.error) {
        throw new Error(data.error || "Request failed");
      }

      setResponse(data.response);

      // A task action can ask for audio (radio, a local track) or for the
      // current audio to stop. Honest either way: the URL only exists when the
      // backend actually started something.
      if (data.stop_media) {
        stopMediaUrl();
      } else {
        const mediaUrl = data.audio_url || data.radio_stream_url;
        if (mediaUrl) playMediaUrl(mediaUrl);
      }
    } catch (error) {
      console.error(error);
      setResponse("Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  // Dev-only: Avatar preview full-screen overlay. Checked BEFORE the auth
  // gates so the preview can be opened without a running backend.
  if (showAvatarPreview) {
    return (
      <div className="app-container" style={{ minHeight: "100vh" }}>
        <AvatarPreview />
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="app-container loading">
        <div className="loading-spinner" />
        <p>Loading...</p>
      </div>
    );
  }

  // Show login page if auth is enabled and no user logged in
  if (authEnabled && !user) {
    return <LoginPage />;
  }


  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Sahaara</h1>
        <p className="app-subtitle">Consent-based AI companion for dementia care</p>
        {(authEnabled && user) || showCaregiverTabs ? (
          <div className="header-actions">
            {authEnabled && user && (
              <>
                <span className="user-badge">{user.username} ({user.role})</span>
                <button onClick={logout} className="logout-button">Logout</button>
              </>
            )}
            {showCaregiverTabs && <PushButton />}
          </div>
        ) : null}
      </header>

      <TaskAlarm />

      {showCaregiverTabs && (
        <nav className="app-tabs">
          <button
            className={`tab ${view === "live" ? "active" : ""}`}
            onClick={() => { setView("live"); setShowChat(false); }}
          >
            Live Session
          </button>
          <button
            className={`tab ${view === "summary" ? "active" : ""}`}
            onClick={() => { setView("summary"); setShowChat(false); }}
          >
            Daily Summary
          </button>
          <button
            className={`tab ${view === "suggestions" ? "active" : ""}`}
            onClick={() => { setView("suggestions"); setShowChat(false); }}
          >
            Suggested Facts
          </button>
          <button
            className={`tab ${view === "profile" ? "active" : ""}`}
            onClick={() => { setView("profile"); setShowChat(false); }}
          >
            Profile
          </button>
        </nav>
      )}

      {view === "live" && (
        <VoiceStateProvider>
          <div className="app-main">
            {/* Asha's view - Voice Orb / Avatar (always accessible) */}
            <AshaVisual />

            {/* Rohan's view - Escalation Ladder (caregiver only) */}
            {showCaregiverTabs && (
              <section className="caregiver-view">
                <h2 className="view-title">Rohan's View</h2>
                <ErrorBoundary name="EscalationLadder">
                  <EscalationLadder focusAlertId={focusAlertId} />
                </ErrorBoundary>
              </section>
            )}
          </div>
        </VoiceStateProvider>
      )}

      {showCaregiverTabs && view === "summary" && (
        <ErrorBoundary name="DailySummary">
          <DailySummaryScreen />
        </ErrorBoundary>
      )}

      {showCaregiverTabs && view === "suggestions" && (
        <div className="app-main" style={{ display: "block", padding: "0 16px" }}>
          <h2 className="view-title">Suggested Facts</h2>
          <ErrorBoundary name="SuggestedFacts">
            <SuggestedFacts />
          </ErrorBoundary>
        </div>
      )}

      {showCaregiverTabs && view === "profile" && (
        <div className="app-main" style={{ display: "block", padding: "0 16px" }}>
          <h2 className="view-title">Profile</h2>
          <ErrorBoundary name="ProfileForm">
            <ProfileForm />
          </ErrorBoundary>
        </div>
      )}

      {/* Collapsible chat fallback - always accessible for Asha */}
      <button
        className="chat-toggle"
        onClick={() => setShowChat(!showChat)}
        aria-expanded={showChat}
      >
        {showChat ? "Hide Chat" : "Show Chat"} Fallback
      </button>

      {showChat && (
        <div className="chat-fallback">
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") sendMessage(); }}
            placeholder="Talk to Sahaara (text)..."
          />
          <button onClick={sendMessage} disabled={loading}>
            {loading ? "Thinking..." : "Send"}
          </button>
          {response && (
            <p className="chat-response">
              <strong>Sahaara:</strong> {response}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;