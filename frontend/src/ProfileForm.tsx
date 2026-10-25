import { useEffect, useState } from "react";
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

// --- Defensive normalization -------------------------------------------
// The API can serve a corrupted profile (sections as empty strings instead
// of objects). Without this, ANY unexpected shape crashed ProfileForm's
// render and — since it had no ErrorBoundary — unmounted the whole app
// (the Oct 23 "black screen" bug). Now every section is coerced to a
// renderable shape no matter what arrives.
const isObj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const asStr = (v: unknown) => (typeof v === "string" ? v : "");
const asStrList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

function normalizeProfile(raw: unknown, profileId: string): ProfileData {
  const r = isObj(raw) ? raw : {};
  const name = isObj(r.name) ? r.name : {};
  const life = isObj(r.life_story) ? r.life_story : {};
  const likes = isObj(r.likes) ? r.likes : {};
  const routine = isObj(r.daily_routine) ? r.daily_routine : {};
  const meals = isObj(routine.meals) ? routine.meals : {};
  const avoid = isObj(r.avoid) ? r.avoid : {};
  const rawTopics = Array.isArray(avoid.topics) ? avoid.topics : [];
  const topics: TopicSetting[] = rawTopics.map((t) =>
    typeof t === "string"
      ? { topic: t, setting: "avoid" as const, redirect_to: "", notes: "" }
      : {
          topic: isObj(t) ? asStr(t.topic) : "",
          setting: isObj(t) && (t.setting === "avoid" || t.setting === "redirect" || t.setting === "allow_with_care") ? t.setting : "avoid",
          redirect_to: isObj(t) ? asStr(t.redirect_to) : "",
          notes: isObj(t) ? asStr(t.notes) : "",
        }
  );
  const medRaw = routine.medication_times;
  const medication_times = Array.isArray(medRaw)
    ? asStrList(medRaw)
    : typeof medRaw === "string"
      ? medRaw.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
  return {
    profile_id: asStr(r.profile_id) || profileId,
    name: {
      full_name: asStr(name.full_name),
      preferred_address: asStr(name.preferred_address),
      notes: asStr(name.notes),
    },
    family: Array.isArray(r.family)
      ? r.family.map((m) => ({
          name: isObj(m) ? asStr(m.name) : "",
          relationship: isObj(m) ? asStr(m.relationship) : "",
          notes: isObj(m) ? asStr(m.notes) : "",
          memory_box_clip: isObj(m) && typeof m.memory_box_clip === "string" ? m.memory_box_clip : null,
        }))
      : [],
    life_story: {
      hometown: asStr(life.hometown),
      work: asStr(life.work),
      key_events: asStrList(life.key_events),
    },
    likes: {
      music: asStrList(likes.music),
      food: asStrList(likes.food),
      topics: asStrList(likes.topics),
      routines: asStrList(likes.routines),
    },
    avoid: { topics },
    daily_routine: {
      wake_time: asStr(routine.wake_time) || "07:00",
      meals: {
        breakfast: asStr(meals.breakfast),
        lunch: asStr(meals.lunch),
        dinner: asStr(meals.dinner),
      },
      medication_times,
    },
  };
}

export default function ProfileForm() {
  const [profile, setProfile] = useState<ProfileData>(emptyProfile);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProfile();
  }, []);

  const fetchProfile = async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/profile/current");
      const data = await res.json();
      if (data.data) {
        // Coerce every section to a renderable shape (see normalizeProfile)
        setProfile(normalizeProfile(data.data, data.profile_id || "asha_demo_001"));
      }
    } catch (err) {
      console.error("Failed to fetch profile:", err);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    try {
      await fetch(`http://127.0.0.1:8000/profile/${profile.profile_id || "asha_demo_001"}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
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