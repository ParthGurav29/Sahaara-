import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import "./DailySummary.css";

interface Suggestion {
  suggestion_id: string;
  timestamp: number;
  timestamp_iso: string;
  type: string;
  text: string;
  suggested_field: string;
  suggested_value: string;
  source_turn: string;
  confidence: string;
}

const FIELD_LABELS: Record<string, string> = {
  "likes.music": "Music they like",
  "likes.topics": "Topics they enjoy",
  "likes.food": "Food/drink they like",
  "key_events": "Life story event",
  "daily_routine": "Daily routine",
  "family": "Person mentioned",
};

export default function SuggestedFacts() {
  const { authEnabled } = useAuth();
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchSuggestions = async () => {
    
    try {
      const res = await apiFetch("/suggestions", {
      });
      const data = await res.json();
      setSuggestions(data.suggestions);
    } catch (err) {
      console.error("Failed to fetch suggestions:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSuggestions();
  }, [authEnabled]);

  const approve = async (id: string) => {
    
    try {
      await apiFetch(`/suggestions/${id}/approve`, {
        method: "POST",
      });
      fetchSuggestions();
    } catch (err) {
      console.error("Approve failed:", err);
    }
  };

  const reject = async (id: string) => {
    
    try {
      await apiFetch(`/suggestions/${id}/reject`, {
        method: "POST",
      });
      fetchSuggestions();
    } catch (err) {
      console.error("Reject failed:", err);
    }
  };

  const formatField = (field: string) => FIELD_LABELS[field] || field;

  if (loading) {
    return <p className="suggestions-loading">Loading suggestions...</p>;
  }

  if (suggestions.length === 0) {
    return (
      <div className="suggestions-empty">
        <p>No pending suggestions.</p>
        <p className="suggestions-hint">
          When Asha mentions something new — a song, a memory, a routine — Sahaara
          will suggest it here for your review. Nothing is added without your approval.
        </p>
      </div>
    );
  }

  return (
    <div className="suggestions-list">
      <h3 className="suggestions-title">
        Suggested Facts ({suggestions.length})
      </h3>
      <p className="suggestions-subtitle">
        Review and approve facts before they enter Asha's profile.
      </p>
      {suggestions.map((s) => (
        <div key={s.suggestion_id} className="suggestion-card">
          <div className="suggestion-card__meta">
            <span className={`suggestion-card__type suggestion-card__type--${s.type}`}>
              {s.type}
            </span>
            <span className="suggestion-card__field">
              → {formatField(s.suggested_field)}
            </span>
          </div>
          <p className="suggestion-card__value">
            <strong>"{s.suggested_value}"</strong>
          </p>
          <p className="suggestion-card__source">
            Asha said: "{s.source_turn.slice(0, 100)}{s.source_turn.length > 100 ? "..." : ""}"
          </p>
          <div className="suggestion-card__actions">
            <button
              className="suggestion-btn suggestion-btn--approve"
              onClick={() => approve(s.suggestion_id)}
            >
              ✓ Approve
            </button>
            <button
              className="suggestion-btn suggestion-btn--reject"
              onClick={() => reject(s.suggestion_id)}
            >
              ✗ Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}