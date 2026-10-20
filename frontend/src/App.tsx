import { Component, useState } from "react";

import VoiceOrb from "./VoiceOrb";
import EscalationLadder from "./EscalationLadder";
import DailySummaryScreen from "./DailySummaryScreen";
import SuggestedFacts from "./SuggestedFacts";
import "./App.css";

class ErrorBoundary extends Component<
  { children: React.ReactNode; name: string },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode; name: string }) {
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

function App() {
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"live" | "summary" | "suggestions">("live");
  const [showChat, setShowChat] = useState(false);

  async function sendMessage() {
    if (!message.trim()) return;

    setLoading(true);
    setResponse("");

    try {
      const res = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const data = await res.json();

      if (!res.ok || data.error) {
        throw new Error(data.error || "Request failed");
      }

      setResponse(data.response);
    } catch (error) {
      console.error(error);
      setResponse("Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Sahaara</h1>
        <p className="app-subtitle">Consent-based AI companion for dementia care</p>
      </header>

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
      </nav>

      {view === "live" && (
        <div className="app-main">
          {/* Asha's view - Voice Orb */}
          <section className="asha-view">
            <h2 className="view-title">Asha's View</h2>
            <ErrorBoundary name="VoiceOrb">
              <VoiceOrb />
            </ErrorBoundary>
          </section>

          {/* Rohan's view - Escalation Ladder */}
          <section className="caregiver-view">
            <h2 className="view-title">Rohan's View</h2>
            <ErrorBoundary name="EscalationLadder">
              <EscalationLadder />
            </ErrorBoundary>
          </section>
        </div>
      )}

      {view === "summary" && (
        <DailySummaryScreen />
      )}

      {view === "suggestions" && (
        <div className="app-main" style={{ display: "block", padding: "0 16px" }}>
          <h2 className="view-title">Suggested Facts</h2>
          <SuggestedFacts />
        </div>
      )}

      {/* Collapsible chat fallback */}
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

export default App;