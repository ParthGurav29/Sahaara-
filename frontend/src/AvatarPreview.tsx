import { useState, useEffect } from "react";
import SahaaraAvatar, { type AvatarState, type AvatarTier } from "./SahaaraAvatar";
import "./AvatarPreview.css";

const STATES: AvatarState[] = ["idle", "listening", "thinking", "speaking", "error"];
const TIERS: AvatarTier[] = ["green", "yellow", "orange", "red"];

const STATE_LABELS: Record<AvatarState, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Error",
};

const TIER_LABELS: Record<AvatarTier, string> = {
  green: "Green (baseline)",
  yellow: "Yellow (slower)",
  orange: "Orange (calmer)",
  red: "Red (calmest)",
};

const STATE_COLOR: Record<AvatarState, string> = {
  idle: "#C9C2B8",
  listening: "#7C93A8",
  thinking: "#A98FC7",
  speaking: "#E8A85C",
  error: "#C2716B",
};

const TIER_SPEED: Record<AvatarTier, number> = { green: 1.0, yellow: 0.8, orange: 0.6, red: 0.4 };
const TIER_GLOW: Record<AvatarTier, number> = { green: 0.40, yellow: 0.30, orange: 0.20, red: 0.15 };
const TIER_MOUTH: Record<AvatarTier, number> = { green: 1.0, yellow: 0.9, orange: 0.7, red: 0.5 };

export default function AvatarPreview() {
  const [state, setState] = useState<AvatarState>("idle");
  const [tier, setTier] = useState<AvatarTier>("green");
  const [level, setLevel] = useState(0);
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [simulatedLevel, setSimulatedLevel] = useState(0);

  // Simulate speaking audio level
  useEffect(() => {
    if (!autoSpeak || state !== "speaking") {
      setSimulatedLevel(0);
      return;
    }
    let t = 0;
    const id = setInterval(() => {
      t += 0.05;
      // Simulate speech envelope: random-ish but smooth
      const v = Math.abs(Math.sin(t * 2.3)) * 0.6 + Math.abs(Math.sin(t * 1.7)) * 0.3 + 0.1;
      setSimulatedLevel(Math.min(1, v));
    }, 50);
    return () => clearInterval(id);
  }, [autoSpeak, state]);

  const effectiveLevel = state === "speaking" && autoSpeak ? simulatedLevel : level;

  return (
    <div className="avatar-preview">
      <h2>Sahaara Avatar — Dev Preview</h2>
      <p className="avatar-preview__desc">
        Test the avatar states, tiers, and audio reactivity. <strong>Only works in development mode.</strong>
      </p>

      <SahaaraAvatar state={state} level={effectiveLevel} tier={tier} />

      <div className="avatar-preview__status">
        <strong>Current:</strong> {STATE_LABELS[state]} | {TIER_LABELS[tier]} | Level: {Math.round(effectiveLevel * 100)}%
      </div>

      <div className="avatar-preview__controls">
        <div className="avatar-preview__control">
          <label>State</label>
          <select value={state} onChange={(e) => setState(e.target.value as AvatarState)}>
            {STATES.map((s) => (
              <option key={s} value={s}>{STATE_LABELS[s]}</option>
            ))}
          </select>
        </div>

        <div className="avatar-preview__control">
          <label>Tier</label>
          <select value={tier} onChange={(e) => setTier(e.target.value as AvatarTier)}>
            {TIERS.map((t) => (
              <option key={t} value={t}>{TIER_LABELS[t]}</option>
            ))}
          </select>
        </div>

        <div className="avatar-preview__control">
          <label>Audio Level (0–1)</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={level}
            onChange={(e) => setLevel(Number(e.target.value))}
            disabled={autoSpeak || state !== "speaking"}
          />
          <span>{Math.round(level * 100)}%</span>
        </div>

        <div className="avatar-preview__control">
          <label>
            <input
              type="checkbox"
              checked={autoSpeak}
              onChange={(e) => setAutoSpeak(e.target.checked)}
              disabled={state !== "speaking"}
            />
            Auto-speak simulation
          </label>
        </div>
      </div>

      <details className="avatar-preview__css-vars">
        <summary>CSS Custom Properties (debug)</summary>
        <pre>
{JSON.stringify({
  "--orb-color": STATE_COLOR[state],
  "--anim-speed": TIER_SPEED[tier],
  "--glow-opacity": TIER_GLOW[tier],
  "--mouth-scale": TIER_MOUTH[tier],
  "--mouth-open": effectiveLevel,
  "--eye-opacity": state === "idle" ? 0.15 : 0.6,
}, null, 2)}
        </pre>
      </details>
    </div>
  );
}