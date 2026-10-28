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
import { useEffect, useRef } from "react";
import { useVoiceLevel, useVoiceState } from "./VoiceStateContext";
import { useVoiceSession, type VoiceSessionState } from "./useVoiceSession";
import OrbCharacter from "./OrbCharacter";
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

// Mouth geometry is owned by OrbCharacter (two nested groups: the calm loop and
// the real amplitude). This shell only publishes the tier + state variables.

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
 *
 * Everything visible is drawn by `OrbCharacter`; this component owns the SVG
 * shell, the CSS custom properties and the two runtime switches (hidden tab,
 * reduced motion) that CSS cannot observe on its own.
 */
export function AvatarVisual({ state, level = 0, tier = "green", size = 130 }: AvatarVisualProps) {
  const svgRef = useRef<SVGSVGElement>(null);

  const animSpeed = TIER_SPEED[tier];
  const glowOpacity = TIER_GLOW[tier];
  const mouthScale = TIER_MOUTH[tier];

  // Apply CSS custom properties for the animations.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const root = svg.style;
    root.setProperty("--orb-color", STATE_COLOR[state]);
    root.setProperty("--anim-speed", String(animSpeed));
    root.setProperty("--glow-opacity", String(glowOpacity));
    root.setProperty("--mouth-scale", String(mouthScale));
  }, [state, animSpeed, glowOpacity, mouthScale]);

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
      viewBox="0 0 400 400"
      width={size}
      height={size}
      style={{ width: "100%", maxWidth: size, height: "auto" }}
      aria-hidden="true"
      focusable="false"
    >
      <OrbCharacter state={state} level={level} />
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
