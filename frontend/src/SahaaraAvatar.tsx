/* SahaaraAvatar — the single voice control and voice visual.
 *
 * This replaced the orb entirely. It owns the live session through
 * `useVoiceSession()` (click = start/stop, exactly like the orb did) and the
 * SVG below reacts to real TTS amplitude published by the playback
 * AnalyserNode.
 *
 * Design constraints that must not be broken (see docs/avatar_design.md):
 *  - Abstract only. Never a person, never a likeness of Priya/Rohan/Vikram.
 *  - Escalation makes it CALMER, never alarming: higher tiers slow the motion,
 *    dim the glow and shrink the mouth. No urgent colours, shaking or flashing.
 *  - Output-only. No camera, no face tracking, no lip-sync tooling.
 *  - `prefers-reduced-motion` is respected; animation pauses on a hidden tab.
 */
import { useEffect, useRef, useState } from "react";
import { useVoiceLevel, useVoiceState } from "./VoiceStateContext";
import { useVoiceSession, type VoiceSessionState } from "./useVoiceSession";
import "./SahaaraAvatar.css";

export type AvatarState = VoiceSessionState;
export type AvatarTier = "green" | "yellow" | "orange" | "red";

const TIER_SPEED: Record<AvatarTier, number> = {
  green: 1.0,
  yellow: 0.8,
  orange: 0.6,
  red: 0.4,
};

const TIER_GLOW: Record<AvatarTier, number> = {
  green: 0.4,
  yellow: 0.3,
  orange: 0.2,
  red: 0.15,
};

const TIER_MOUTH: Record<AvatarTier, number> = {
  green: 1.0,
  yellow: 0.9,
  orange: 0.7,
  red: 0.5,
};

const STATE_COLOR: Record<AvatarState, string> = {
  idle: "#C9C2B8", // neutral stone — nothing is happening yet
  listening: "#7C93A8", // slate blue — attentive
  thinking: "#A98FC7", // muted lavender — working on it
  speaking: "#E8A85C", // warm amber — present, speaking
  error: "#C2716B", // muted clay-red — something's wrong, not alarming
};

const STATE_LABEL: Record<AvatarState, string> = {
  idle: "Tap to talk",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Connection lost — tap to retry",
};

// A refused microphone is the most likely failure on a phone, and saying
// "connection lost" would send someone after the wrong problem.
const MIC_LABEL = "Microphone unavailable — tap to retry";

// Mouth geometry, in the 130×130 viewBox units. The mouth line is 46 units
// long (x1=42 → x2=88); MOUTH_REST keeps a short calm line when silent and
// MOUTH_FULL is the widest it can open.
const MOUTH_PATH_LENGTH = 46;
const MOUTH_REST = 10;
const MOUTH_FULL = 46;

export interface AvatarVisualProps {
  state: AvatarState;
  /** 0–1 playback amplitude. 0 while silent. */
  level?: number;
  tier?: AvatarTier;
  /** Rendered width in CSS px (the SVG stays square and scales). */
  size?: number;
}

/**
 * Pure visual. No session, no click handling — the dev preview drives it
 * directly, and the interactive `SahaaraAvatar` below wraps it in a button.
 */
export function AvatarVisual({ state, level = 0, tier = "green", size = 130 }: AvatarVisualProps) {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  const animSpeed = TIER_SPEED[tier];
  const glowOpacity = TIER_GLOW[tier];
  const mouthScale = TIER_MOUTH[tier];

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setPrefersReducedMotion(mediaQuery.matches);
    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);

  // Apply CSS custom properties for the animations.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const root = svg.style;
    root.setProperty("--orb-color", STATE_COLOR[state]);
    root.setProperty("--anim-speed", String(animSpeed));
    root.setProperty("--glow-opacity", String(glowOpacity));
    root.setProperty("--mouth-scale", String(mouthScale));

    const clamped = Math.max(0, Math.min(1, level));
    root.setProperty("--mouth-open", String(clamped));
    // Mouth length in path units: a calm resting line that opens with amplitude.
    // Kept symmetric around the centre by strokeDashoffset below.
    root.setProperty(
      "--mouth-dash",
      String(MOUTH_REST + (MOUTH_FULL - MOUTH_REST) * clamped * mouthScale),
    );
    // Eyes read as a soft resting face when idle, brighter when engaged.
    const isActiveState = state !== "idle";
    root.setProperty("--eye-opacity", String(isActiveState ? 0.85 : 0.45));
  }, [state, level, animSpeed, glowOpacity, mouthScale]);

  // Visibility pause (tab hidden). `animation-play-state` does not inherit, so
  // the class has to sit on the SVG and the CSS rule must target its children.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleVisibilityChange = () => {
      svg.classList.toggle("is-paused", document.hidden);
    };
    handleVisibilityChange();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  return (
    <svg
      ref={svgRef}
      className="sahaara-avatar"
      data-state={state}
      viewBox="0 0 130 130"
      width={size}
      height={size}
      style={{ width: "100%", maxWidth: size, height: "auto" }}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id="orbGradient" cx="35%" cy="30%" r="65%">
          <stop offset="0%" stopColor="white" stopOpacity="0.5" />
          <stop offset="100%" stopColor="var(--orb-color)" />
        </radialGradient>
        <radialGradient id="eyeGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#5f5a54" stopOpacity="1" />
          <stop offset="60%" stopColor="#5f5a54" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#5f5a54" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="outerGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--orb-color)" stopOpacity="var(--glow-opacity)" />
          <stop offset="70%" stopColor="var(--orb-color)" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Outer glow ring */}
      <circle
        className="avatar-glow"
        cx="65"
        cy="65"
        r="58"
        fill="url(#outerGlow)"
        style={{ opacity: prefersReducedMotion ? 0 : 1 }}
      />

      {/* Main orb with tilt group */}
      <g className="avatar-orb-group">
        <circle
          className="avatar-orb"
          cx="65"
          cy="65"
          r="55"
          fill="url(#orbGradient)"
          stroke="var(--orb-color)"
          strokeWidth="2"
          strokeOpacity="0.3"
        />

        {/* Eyes — two soft glow points */}
        <circle
          className="avatar-eye"
          cx="40"
          cy="45"
          r="5"
          fill="url(#eyeGlow)"
          style={{ opacity: "var(--eye-opacity)" }}
        />
        <circle
          className="avatar-eye"
          cx="90"
          cy="45"
          r="5"
          fill="url(#eyeGlow)"
          style={{ opacity: "var(--eye-opacity)" }}
        />

        {/* Mouth — a calm line that opens symmetrically with audio level.
            Stroke is the same dark neutral as the eyes: the state colour
            (amber while speaking) is far too low-contrast on its own orb. */}
        <line
          className="avatar-mouth"
          x1="42"
          y1="85"
          x2="88"
          y2="85"
          stroke="#5f5a54"
          strokeWidth="3"
          strokeLinecap="round"
          style={{
            strokeDasharray: "var(--mouth-dash) 46",
            // Centres the dash: the visible run starts at (46 - dash) / 2.
            strokeDashoffset: `calc(var(--mouth-dash) * 0.5 - ${MOUTH_PATH_LENGTH / 2})`,
            opacity: state === "speaking" ? 0.9 : 0.5,
          }}
        />
      </g>

      {/* Reduced motion fallback: colour-only progress ring */}
      {prefersReducedMotion && (
        <circle
          className="avatar-reduced-indicator"
          cx="65"
          cy="65"
          r="55"
          fill="none"
          stroke="var(--orb-color)"
          strokeWidth="3"
          strokeDasharray="345"
          strokeDashoffset={
            state === "idle" ? 345 : state === "listening" ? 200 : state === "thinking" ? 100 : 0
          }
        />
      )}
    </svg>
  );
}

/**
 * The interactive control that sits where the orb used to be. Clicking the
 * avatar starts a voice session; clicking again stops it. Its mouth opens with
 * the real TTS amplitude and it calms down (slower fade, dimmer glow, smaller
 * mouth) as the escalation tier rises.
 */
export default function SahaaraAvatar() {
  const { state, isActive, toggle, initiativePrompt, dismissInitiative, errorKind, audioRef } =
    useVoiceSession();
  const level = useVoiceLevel();
  const { tier } = useVoiceState();

  const label = state === "error" && errorKind === "microphone" ? MIC_LABEL : STATE_LABEL[state];

  return (
    <div
      className="sahaara-avatar-wrap"
      style={{
        ["--orb-color" as string]: STATE_COLOR[state],
        // The live-session ring inherits the tier slowdown too.
        ["--anim-speed" as string]: String(TIER_SPEED[tier]),
      }}
    >
      <button
        type="button"
        className={`sahaara-avatar-button${isActive ? " is-active" : ""}`}
        onClick={toggle}
        aria-pressed={isActive}
        aria-label={isActive ? "Stop talking with Sahaara" : "Start talking with Sahaara"}
      >
        <AvatarVisual state={state} level={level} tier={tier} size={168} />
      </button>

      <p className="sahaara-avatar__label" role="status" aria-live="polite">
        {label}
      </p>

      {/* Hidden audio element for radio streaming */}
      <audio ref={audioRef} />

      {initiativePrompt && (
        <div className="voice-initiative-prompt" role="status" aria-live="polite">
          {initiativePrompt}
          <button
            className="voice-initiative-dismiss"
            onClick={dismissInitiative}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
