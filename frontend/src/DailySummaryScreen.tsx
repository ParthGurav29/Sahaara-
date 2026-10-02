import { useEffect, useState } from "react";
import "./DailySummary.css";

interface DailySummary {
  profile_id: string;
  date: string;
  total_turns: number;
  repeat_count: number;
  distress_episodes: number;
  tier_changes: number;
  highest_tier: "green" | "yellow" | "orange" | "red";
  consent_requests: number;
  consent_accepted: number;
  consent_declined: number;
  consent_timeout: number;
  fallbacks_triggered: number;
  summary_text: string;
  noise_reduction_note: string;
}

const TIER_LABELS: Record<string, string> = {
  green: "GREEN",
  yellow: "YELLOW",
  orange: "ORANGE",
  red: "RED",
};

const TIER_COLORS: Record<string, string> = {
  green: "#7C93A8",
  yellow: "#E8A85C",
  orange: "#D47B2E",
  red: "#C2716B",
};

export default function DailySummaryScreen() {
  const [summary, setSummary] = useState<DailySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSummary();
  }, []);

  const fetchSummary = async () => {
    try {
      setLoading(true);
      const res = await fetch("http://127.0.0.1:8000/daily-summary");
      if (!res.ok) throw new Error("Failed to fetch summary");
      const data = await res.json();
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="daily-summary">
        <div className="loading">Generating summary...</div>
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="daily-summary">
        <div className="error">Error: {error || "No summary available"}</div>
      </div>
    );
  }

  return (
    <div className="daily-summary">
      <header className="summary-header">
        <h1>Daily Summary</h1>
        <p className="summary-date">{summary.date}</p>
        <p className="noise-reduction-note">{summary.noise_reduction_note}</p>
      </header>

      <div className="summary-content">
        {/* Main Summary */}
        <section className="summary-section main-summary">
          <h2>Summary</h2>
          <p className="summary-text">{summary.summary_text}</p>
        </section>

        {/* Key Metrics */}
        <section className="summary-section metrics">
          <h2>Key Metrics</h2>
          <div className="metrics-grid">
            <div className="metric-card">
              <span className="metric-value">{summary.total_turns}</span>
              <span className="metric-label">Total Turns</span>
            </div>
            <div className="metric-card">
              <span className="metric-value">{summary.repeat_count}</span>
              <span className="metric-label">Repeated Questions</span>
            </div>
            <div className="metric-card">
              <span className="metric-value">{summary.distress_episodes}</span>
              <span className="metric-label">Distress Episodes</span>
            </div>
            <div className="metric-card">
              <span className="metric-value">{summary.tier_changes}</span>
              <span className="metric-label">Tier Changes</span>
            </div>
            <div className="metric-card">
              <span className="metric-value" style={{ color: TIER_COLORS[summary.highest_tier] }}>
                {TIER_LABELS[summary.highest_tier]}
              </span>
              <span className="metric-label">Highest Tier</span>
            </div>
            <div className="metric-card">
              <span className="metric-value">{summary.fallbacks_triggered}</span>
              <span className="metric-label">Fallbacks</span>
            </div>
          </div>
        </section>

        {/* Consent Details */}
        {summary.consent_requests > 0 && (
          <section className="summary-section consent">
            <h2>Consent Interactions</h2>
            <div className="consent-grid">
              <div className="consent-card accepted">
                <span className="consent-value">{summary.consent_accepted}</span>
                <span className="consent-label">Accepted</span>
              </div>
              <div className="consent-card declined">
                <span className="consent-value">{summary.consent_declined}</span>
                <span className="consent-label">Declined</span>
              </div>
              <div className="consent-card timeout">
                <span className="consent-value">{summary.consent_timeout}</span>
                <span className="consent-label">Timeout</span>
              </div>
            </div>
          </section>
        )}

        {/* Design Note */}
        <section className="summary-section design-note">
          <h2>Design Philosophy</h2>
          <p>
            This summary illustrates <strong>noise reduction</strong>: 
            instead of notifying the caregiver every time a question is repeated 
            or distress is detected, Sahaara consolidates the session into one 
            calm, human-readable summary. The raw event log (dozens of events) 
            becomes one actionable overview — by design, not by measurement.
          </p>
        </section>
      </div>
    </div>
  );
}