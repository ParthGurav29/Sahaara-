import { useEffect, useState } from "react";

import { AuthProvider, useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import { playMediaUrl, stopMediaUrl } from "./mediaPlayer";
import { unlockAlarmAudio } from "./alarm";
import LoginPage from "./LoginPage";
import { VoiceStateProvider } from "./VoiceStateContext";
import OrbAvatar from "./OrbAvatar";
import { useVoiceSession } from "./useVoiceSession";
import EscalationLadder from "./EscalationLadder";
import DailySummaryScreen from "./DailySummaryScreen";
import ReviewInbox from "./ReviewInbox";
import ProfileForm from "./ProfileForm";
import AvatarPreview from "./AvatarPreview";
import TaskAlarm from "./TaskAlarm";
import CompanionView from "./CompanionView";
import ChooserView from "./ChooserView";
import ErrorBoundary from "./ErrorBoundary";
import { parseAlertId, withoutAlertParam } from "./deepLink";
import { useRoute, navigateTo } from "./useRoute";
import "./App.css";

/* Voice fallback for error boundary */
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

/* Asha's avatar in caregiver Live tab */
function AshaVisual() {
  const { isActive, toggle } = useVoiceSession();

  return (
    <section className="asha-view">
      <ErrorBoundary name="OrbAvatar" fallback={<VoiceFallbackControl />}>
        <OrbAvatar size={240} onToggle={toggle} isActive={isActive} />
      </ErrorBoundary>
    </section>
  );
}

/* Companion crash fallback */
function CompanionCrashFallback() {
  return (
    <div className="companion-shell companion-shell--error">
      <p>Sahaara is resting for a moment.</p>
      <button type="button" onClick={() => window.location.reload()}>
        Try again
      </button>
    </div>
  );
}

/**
 * The caregiver experience (dashboard + hidden demo layout).
 *
 * AuthProvider wraps this branch. `/companion` and `/choose` never mount it.
 */
function CaregiverApp({ demoSplit, showChooser }: { demoSplit: boolean; showChooser: boolean }) {
  const { user, isLoading, authEnabled } = useAuth();
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"live" | "summary" | "suggestions" | "profile">("live");
  const [focusAlertId, setFocusAlertId] = useState<string | null>(() =>
    parseAlertId(window.location.search),
  );
  const [showChat, setShowChat] = useState(false);
  const [showAvatarPreview, setShowAvatarPreview] = useState(false);
  const [suggestionsCount, setSuggestionsCount] = useState(0);

  const fetchSuggestionsCount = async () => {
    try {
      const res = await apiFetch("/suggestions", {});
      const data = await res.json();
      setSuggestionsCount(data.suggestions?.length || 0);
    } catch (err) {
      console.error("Failed to fetch suggestions count:", err);
    }
  };

  useEffect(() => {
    if (!focusAlertId) return;
    window.history.replaceState({}, "", withoutAlertParam(window.location.href));
  }, [focusAlertId]);

  useEffect(() => {
    const unlock = () => unlockAlarmAudio();
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

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

  useEffect(() => {
    if (import.meta.env.DEV) {
      const params = new URLSearchParams(window.location.search);
      if (params.get("avatar-preview") === "true") {
        setShowAvatarPreview(true);
        params.delete("avatar-preview");
        window.history.replaceState({}, "", `${window.location.pathname}${params.toString() ? "?" + params.toString() : ""}`);
      }
    }
  }, []);

  const showCaregiverTabs = !authEnabled || (user?.role === "caregiver");

  useEffect(() => {
    if (!showCaregiverTabs) return;
    fetchSuggestionsCount();
    const interval = setInterval(fetchSuggestionsCount, 30000);
    return () => clearInterval(interval);
  }, [showCaregiverTabs, authEnabled]);

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

  // Show chooser after login
  if (showChooser) {
    return <ChooserView />;
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div className="wordmark">Sahaara</div>
          <span className="muted">Asha's dashboard</span>
        </div>
        <nav className="app-tabs" role="tablist">
          <button
            className={`tab ${view === "live" ? "active" : ""}`}
            role="tab"
            onClick={() => { setView("live"); setShowChat(false); }}
            aria-selected={view === "live"}
          >
            Today
          </button>
          <button
            className={`tab ${view === "summary" ? "active" : ""}`}
            role="tab"
            onClick={() => { setView("summary"); setShowChat(false); }}
            aria-selected={view === "summary"}
          >
            Alerts
            {suggestionsCount > 0 && (
              <span className="tab-badge">{suggestionsCount}</span>
            )}
          </button>
          <button
            className={`tab ${view === "suggestions" ? "active" : ""}`}
            role="tab"
            onClick={() => { setView("suggestions"); setShowChat(false); }}
            aria-selected={view === "suggestions"}
          >
            Profile
            {suggestionsCount > 0 && (
              <span className="tab-badge">{suggestionsCount}</span>
            )}
          </button>
        </nav>
        <button className="btn ghost sm" onClick={() => navigateTo("/choose")}>
          Switch mode
        </button>
      </header>

      <TaskAlarm />

      {view === "live" && (
        <VoiceStateProvider>
          <div className={`app-main${demoSplit ? "" : " app-main--caregiver"}`}>
            {/* Avatar shown in both demo-split and normal caregiver Live tab */}
            {(demoSplit || showCaregiverTabs) && <AshaVisual />}

            {/* Rohan's view - Escalation Ladder (caregiver only) */}
            {showCaregiverTabs && (
              <section className="caregiver-view">
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
          <h2 className="view-title">Review</h2>
          <ErrorBoundary name="ReviewInbox">
            <ReviewInbox />
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

      {/* Collapsible chat fallback - caregiver/demo side only. Not on /companion. */}
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

/**
 * Root app with route split.
 *
 * Routes:
 * - /companion          -> CompanionView (no auth, full screen)
 * - /choose             -> ChooserView (after login, pick destination)
 * - /caregiver          -> CaregiverApp (dashboard)
 * - /                   -> LoginPage (if auth enabled) or redirect to /caregiver
 * - /?demo=split        -> CaregiverApp with demoSplit=true
 */
function App() {
  const route = useRoute();

  // Show companion directly (no auth)
  if (route === "companion") {
    return (
      <ErrorBoundary name="CompanionView" fallback={<CompanionCrashFallback />}>
        <VoiceStateProvider>
          <CompanionView />
        </VoiceStateProvider>
      </ErrorBoundary>
    );
  }

  // Show chooser (requires auth)
  if (route === "choose") {
    return (
      <AuthProvider>
        <ChooserView />
      </AuthProvider>
    );
  }

  // Caregiver dashboard (requires auth)
  return (
    <AuthProvider>
      <CaregiverAppWithAuth demoSplit={route === "demo-split"} />
    </AuthProvider>
  );
}

function CaregiverAppWithAuth({ demoSplit }: { demoSplit: boolean }) {
  const { authEnabled, user } = useAuth();
  const showChooser = Boolean(authEnabled && user && !window.location.search.includes("demo=split") && window.location.pathname === "/");
  return <CaregiverApp demoSplit={demoSplit} showChooser={showChooser} />;
}

export default App;