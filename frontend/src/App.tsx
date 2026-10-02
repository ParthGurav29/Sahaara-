import { useState } from "react";

import VoiceOrb from "./VoiceOrb";
import EscalationLadder from "./EscalationLadder";
import DailySummaryScreen from "./DailySummaryScreen";
import "./App.css";

function App() {
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState("");
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"live" | "summary">("live");

  async function sendMessage() {
    if (!message.trim()) return;

    setLoading(true);
    setResponse("");

    try {
      const res = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
        }),
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
          onClick={() => setView("live")}
        >
          Live Session
        </button>
        <button
          className={`tab ${view === "summary" ? "active" : ""}`}
          onClick={() => setView("summary")}
        >
          Daily Summary
        </button>
      </nav>

      {view === "live" && (
        <>
          <div className="app-main">
            {/* Asha's view - Voice Orb */}
            <section className="asha-view">
              <h2 className="view-title">Asha's View</h2>
              <VoiceOrb />
            </section>

            {/* Rohan's view - Escalation Ladder */}
            <section className="caregiver-view">
              <h2 className="view-title">Rohan's View (Caregiver)</h2>
              <EscalationLadder />
            </section>
          </div>

          {/* Text chat fallback */}
          <div className="chat-fallback">
            <input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  sendMessage();
                }
              }}
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
        </>
      )}

      {view === "summary" && (
        <DailySummaryScreen />
      )}
    </div>
  );
}

export default App;