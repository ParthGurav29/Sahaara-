import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { apiFetch } from "./api";
import "./DailySummary.css";

interface FamilyMember {
  name: string;
  relationship: string;
  notes: string;
  memory_box_clip: string | null;
}

interface TopicSetting {
  topic: string;
  setting: "avoid" | "redirect" | "allow_with_care";
  redirect_to: string;
  notes: string;
}

interface ProfileData {
  profile_id: string;
  name: { full_name: string; preferred_address: string; notes: string };
  family: FamilyMember[];
  life_story: { hometown: string; work: string; key_events: string[] };
  likes: { music: string[]; food: string[]; topics: string[]; routines: string[] };
  avoid: { topics: TopicSetting[] };
  daily_routine: { wake_time: string; meals: Record<string, string>; medication_times: string[] };
}

const emptyProfile: ProfileData = {
  profile_id: "",
  name: { full_name: "", preferred_address: "", notes: "" },
  family: [],
  life_story: { hometown: "", work: "", key_events: [] },
  likes: { music: [], food: [], topics: [], routines: [] },
  avoid: { topics: [] },
  daily_routine: { wake_time: "07:00", meals: { breakfast: "", lunch: "", dinner: "" }, medication_times: [] },
};

export default function ProfileForm() {
  const { token, authEnabled } = useAuth();
  const [profile, setProfile] = useState<ProfileData>(emptyProfile);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProfile();
  }, [token, authEnabled]);

  const fetchProfile = async () => {
    if (!authEnabled || !token) return;
    
    try {
      const res = await apiFetch("/profile/current", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (data.data) {
        // Normalize avoid topics to new format
        const avoid = data.data.avoid || {};
        const rawTopics = avoid.topics || [];
        const topics: TopicSetting[] = rawTopics.map((t: any) =>
          typeof t === "string"
            ? { topic: t, setting: "avoid" as const, redirect_to: "", notes: "" }
            : { topic: t.topic || "", setting: t.setting || "avoid", redirect_to: t.redirect_to || "", notes: t.notes || "" }
        );
        setProfile({ ...data.data, avoid: { topics }, profile_id: data.profile_id });
      }
    } catch (err) {
      console.error("Failed to fetch profile:", err);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    if (!authEnabled || !token) return;
    
    try {
      await apiFetch(`/profile/${profile.profile_id || "asha_demo_001"}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ data: profile }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      console.error("Save failed:", err);
    }
  };

  const update = (path: string, value: any) => {
    setProfile((prev) => {
      const copy = JSON.parse(JSON.stringify(prev));
      const keys = path.split(".");
      let obj = copy;
      for (let i = 0; i < keys.length - 1; i++) obj = obj[keys[i]];
      obj[keys[keys.length - 1]] = value;
      return copy;
    });
  };

  if (loading) return <p className="suggestions-loading">Loading profile...</p>;

  return (
    <div className="profile-form">
      <div className="profile-form__header">
        <h3>About {profile.name.preferred_address || "..."}</h3>
        <button className="suggestion-btn suggestion-btn--approve" onClick={save}>
          {saved ? "✓ Saved" : "Save Changes"}
        </button>
      </div>

      {/* Name */}
      <section className="profile-section">
        <h4>Who they are</h4>
        <div className="profile-field">
          <label>Full name</label>
          <input value={profile.name.full_name} onChange={(e) => update("name.full_name", e.target.value)} />
        </div>
        <div className="profile-field">
          <label>Preferred address</label>
          <input value={profile.name.preferred_address} onChange={(e) => update("name.preferred_address", e.target.value)} />
        </div>
        <div className="profile-field">
          <label>Hometown</label>
          <input value={profile.life_story.hometown} onChange={(e) => update("life_story.hometown", e.target.value)} />
        </div>
        <div className="profile-field">
          <label>Work / background</label>
          <input value={profile.life_story.work} onChange={(e) => update("life_story.work", e.target.value)} />
        </div>
      </section>

      {/* Family */}
      <section className="profile-section">
        <h4>Family</h4>
        {profile.family.map((m, i) => (
          <div key={i} className="profile-field-row">
            <input
              value={m.name}
              onChange={(e) => update(`family.${i}.name`, e.target.value)}
              placeholder="Name"
              style={{ width: 100 }}
            />
            <input
              value={m.relationship}
              onChange={(e) => update(`family.${i}.relationship`, e.target.value)}
              placeholder="Relationship"
              style={{ width: 110 }}
            />
            <input
              value={m.notes}
              onChange={(e) => update(`family.${i}.notes`, e.target.value)}
              placeholder="Notes"
              style={{ flex: 1 }}
            />
            <button
              className="profile-remove-btn"
              onClick={() => update("family", profile.family.filter((_, j) => j !== i))}
            >
              ✕
            </button>
          </div>
        ))}
        <button
          className="profile-add-btn"
          onClick={() => update("family", [...profile.family, { name: "", relationship: "", notes: "", memory_box_clip: null }])}
        >
          + Add family member
        </button>
      </section>

      {/* Likes */}
      <section className="profile-section">
        <h4>Likes</h4>
        {(["music", "food", "topics", "routines"] as const).map((cat) => (
          <div key={cat} className="profile-field">
            <label>{cat.charAt(0).toUpperCase() + cat.slice(1)}</label>
            <textarea
              value={(profile.likes[cat] || []).join("\n")}
              onChange={(e) => update(`likes.${cat}`, e.target.value.split("\n").filter(Boolean))}
              rows={2}
              placeholder={`One per line`}
            />
          </div>
        ))}
      </section>

      {/* Daily Routine */}
      <section className="profile-section">
        <h4>Daily routine</h4>
        <div className="profile-field">
          <label>Wake time</label>
          <input value={profile.daily_routine.wake_time} onChange={(e) => update("daily_routine.wake_time", e.target.value)} />
        </div>
        {Object.entries(profile.daily_routine.meals).map(([meal, time]) => (
          <div key={meal} className="profile-field">
            <label>{meal.charAt(0).toUpperCase() + meal.slice(1)}</label>
            <input value={time} onChange={(e) => update(`daily_routine.meals.${meal}`, e.target.value)} />
          </div>
        ))}
        <div className="profile-field">
          <label>Medication times (comma-separated)</label>
          <input
            value={profile.daily_routine.medication_times.join(", ")}
            onChange={(e) => update("daily_routine.medication_times", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
          />
        </div>
      </section>

      {/* Per-topic settings (F4.2) */}
      <section className="profile-section">
        <h4>Sensitive topics</h4>
        <p className="suggestions-hint">
          Control how Sahaara handles each topic. "Avoid" redirects gently.
          "Redirect" pivots to a specific safe topic. "Allow with care" lets the conversation flow.
        </p>
        {profile.avoid.topics.map((t, i) => (
          <div key={i} className="topic-setting-card">
            <div className="profile-field-row">
              <input
                value={t.topic}
                onChange={(e) => update(`avoid.topics.${i}.topic`, e.target.value)}
                placeholder="Topic (e.g. Vikram's death)"
                style={{ flex: 2 }}
              />
              <select
                value={t.setting}
                onChange={(e) => update(`avoid.topics.${i}.setting`, e.target.value)}
              >
                <option value="avoid">Avoid</option>
                <option value="redirect">Redirect</option>
                <option value="allow_with_care">Allow with care</option>
              </select>
              <button
                className="profile-remove-btn"
                onClick={() => update("avoid.topics", profile.avoid.topics.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
            {t.setting === "redirect" && (
              <div className="profile-field">
                <label>Redirect to</label>
                <input
                  value={t.redirect_to}
                  onChange={(e) => update(`avoid.topics.${i}.redirect_to`, e.target.value)}
                  placeholder="e.g. happy memories of teaching"
                />
              </div>
            )}
            <div className="profile-field">
              <label>Notes</label>
              <input
                value={t.notes}
                onChange={(e) => update(`avoid.topics.${i}.notes`, e.target.value)}
                placeholder="Why this setting?"
              />
            </div>
          </div>
        ))}
        <button
          className="profile-add-btn"
          onClick={() =>
            update("avoid.topics", [...profile.avoid.topics, { topic: "", setting: "avoid" as const, redirect_to: "", notes: "" }])
          }
        >
          + Add topic
        </button>
      </section>
    </div>
  );
}