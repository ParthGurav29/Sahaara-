import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import "./ReviewInbox.css";

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

export default function ReviewInbox() {
  const { authEnabled } = useAuth();
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchSuggestions = async () => {
    try {
      const res = await apiFetch("/suggestions", {});
      const data = await res.json();
      setSuggestions(data.suggestions);
    } catch (err) {
      console.error("Failed to fetch suggestions:", err);
    } finally {
      setLoading(false);
    }
  };

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

  // Initial fetch
  useEffect(() => {
    fetchSuggestions();
  }, [authEnabled]);

  // Refresh every 30s while the caregiver view is visible
  useEffect(() => {
    const interval = setInterval(fetchSuggestions, 30000);
    return () => clearInterval(interval);
  }, [authEnabled]);

  if (loading) {
    return <p className="review-loading">Loading review items…</p>;
  }

  if (suggestions.length === 0) {
    return (
      <div className="review-empty">
        <p className="review-empty__text">Nothing to review right now.</p>
        <p className="review-empty__hint">
          While chatting, Sahaara notices things about Asha (a song she likes,
          a memory she shared). Nothing is added to her profile until you
          approve it.
        </p>
      </div>
    );
  }

  return (
    <div className="review-list">
      <p className="review-explainer">
        While chatting, Sahaara notices things about Asha (a song she likes, a
        memory she shared). Nothing is added to her profile until you approve
        it.
      </p>
      <div className="review-cards">
        {suggestions.map((s) => (
          <div key={s.suggestion_id} className="review-card">
            <div className="review-card__meta">
              <span className={`review-card__type review-card__type--${s.type}`}>
                {s.type}
              </span>
              <span className="review-card__field">
                → {formatField(s.suggested_field)}
              </span>
            </div>
            <p className="review-card__value">
              <strong>"{s.suggested_value}"</strong>
            </p>
            <p className="review-card__source">
              Asha said: "{s.source_turn.slice(0, 100)}{
                s.source_turn.length > 100 ? "…" : ""
              }"
            </p>
            <div className="review-card__actions">
              <button
                className="review-btn review-btn--approve"
                onClick={() => approve(s.suggestion_id)}
              >
                ✓ Approve
              </button>
              <button
                className="review-btn review-btn--reject"
                onClick={() => reject(s.suggestion_id)}
              >
                ✗ Reject
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}