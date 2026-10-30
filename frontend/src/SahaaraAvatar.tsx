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
 *  - Output-only. No camera, no face tracking, no ML/video lip-sync. The
 *    mouth only ever follows the app's own TTS playback (A9).
 *  - `prefers-reduced-motion` is respected; animation pauses on a hidden tab.
 */
import { useCallback, useEffect, useRef, type CSSProperties } from "react";
import { useVoiceMouth, useVoiceState, useListenLevel } from "./VoiceStateContext";
import { useVoiceSession, type VoiceSessionState } from "./useVoiceSession";
import HumanCharacter from "./HumanCharacter";
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

// Radio stream auto-reconnect constants
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 3000;

// Mouth geometry is owned by HumanCharacter; this shell publishes the numbers the
// voice session measured (open/wide) as CSS custom properties.

/**
 * The audio-reactive listening ring. Reads the real microphone level (its own
 * context, so only this component re-renders while the level changes) and draws
 * a set of radial "waveform" bars around the head. Only shown while listening;
 * when the mic gate is closed (thinking/speaking) it is not rendered at all, so
 * it can never read as "hearing you" when she is not.
 */
function ListenRing({ state }: { state: AvatarState }) {
  const level = useListenLevel();
  if (state !== "listening") return null;

  // Reduced motion: a static, fixed-height ring instead of a live readout.
  const reduced =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lvl = reduced ? 0.4 : level;

  const bars = [];
  const N = 48;
  for (let i = 0; i < N; i++) {
    const a = (i / N) * Math.PI * 2;
    const wave = 0.5 + 0.5 * Math.sin(i * 0.66); // fixed waveform profile
    const len = (4 + 8 * wave) * (0.3 + lvl * 0.7);
    const x1 = 200 + Math.cos(a) * 86;
    const y1 = 150 + Math.sin(a) * 86;
    const x2 = 200 + Math.cos(a) * (86 + len);
    const y2 = 150 + Math.sin(a) * (86 + len);
    bars.push(
      <line
        key={i}
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke="var(--orb-color, #7C93A8)"
        strokeWidth={2.4}
        strokeLinecap="round"
      />,
    );
  }

  return (
    <g className="listen-ring" style={{ opacity: reduced ? 0.5 : 0.25 + lvl * 0.75 }}>
      {bars}
    </g>
  );
}

export interface AvatarVisualProps {
  state: AvatarState;
  /** 0–1 jaw opening from the playback envelope. 0 while silent. */
  level?: number;
  /** 0–1 mouth width from the spectral brightness. */
  wide?: number;
  tier?: AvatarTier;
  /** Rendered width in CSS px (the SVG stays square and scales). */
  size?: number;
}

/**
 * Pure visual. No session, no click handling — the dev preview drives it
 * directly, and the interactive `SahaaraAvatar` below wraps it in a button.
 *
 * Everything visible is drawn by `HumanCharacter`; this component owns the SVG
 * shell, the CSS custom properties and the two runtime switches (hidden tab,
 * reduced motion) that CSS cannot observe on its own.
 */
export function AvatarVisual({
  state,
  level = 0,
  wide = 0,
  tier = "green",
  size = 130,
}: AvatarVisualProps) {
  const svgRef = useRef<SVGSVGElement>(null);

  const animSpeed = TIER_SPEED[tier];
  const glowOpacity = TIER_GLOW[tier];
  const mouthScale = TIER_MOUTH[tier];

  // A mouth can only be open while she is speaking. Stale values from a
  // finished turn (or a second avatar on the preview page) must never leave a
  // face frozen mid-syllable in idle.
  const speaking = state === "speaking";
  const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

  // Inline rather than set in an effect: the mouth values change every frame
  // while she speaks, and an effect would paint one frame late.
  const vars = {
    "--orb-color": STATE_COLOR[state],
    "--anim-speed": String(animSpeed),
    "--glow-opacity": String(glowOpacity),
    "--mouth-scale": String(mouthScale),
    "--mouth-open": String(speaking ? clamp01(level) : 0),
    "--mouth-wide": String(speaking ? clamp01(wide) : 0),
  } as CSSProperties;

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
      style={{ width: "100%", maxWidth: size, height: "auto", ...vars }}
      aria-hidden="true"
      focusable="false"
    >
      <HumanCharacter state={state} />
      <ListenRing state={state} />
    </svg>
  );
}

interface SahaaraAvatarProps {
  size?: number;
  // Voice session props (passed from parent that owns the session)
  state: VoiceSessionState;
  isActive: boolean;
  toggle: () => void;
  initiativePrompt: string | null;
  dismissInitiative: () => void;
  errorKind: "connection" | "microphone" | null;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  mouth: { open: number; wide: number };
  tier: AvatarTier;
}

/**
 * The interactive control that sits where the orb used to be. Clicking the
 * avatar starts a voice session; clicking again stops it. Its mouth opens with
 * the real TTS amplitude and it calms down (slower fade, dimmer glow, smaller
 * mouth) as the escalation tier rises.
 */
export function SahaaraAvatar({
  size = 168,
  state,
  isActive,
  toggle,
  initiativePrompt,
  dismissInitiative,
  errorKind,
  audioRef,
  mouth,
  tier,
}: SahaaraAvatarProps) {
  const label = state === "error" && errorKind === "microphone" ? MIC_LABEL : STATE_LABEL[state];

  // Radio stream auto-reconnect logic
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);

  const handleAudioError = useCallback((e: React.SyntheticEvent<HTMLAudioElement>) => {
    const audio = e.currentTarget;
    console.error("[VoiceSession] Radio stream error:", audio.error);
    if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS && audio.src) {
      reconnectAttemptsRef.current++;
      console.log(`[VoiceSession] Radio stream error, reconnecting (attempt ${reconnectAttemptsRef.current}/${MAX_RECONNECT_ATTEMPTS})...`);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = window.setTimeout(() => {
        if (audio.src) {
          audio.load();
          audio.play().catch(err => console.error("[VoiceSession] Reconnect play failed:", err));
        }
      }, RECONNECT_DELAY_MS);
    } else {
      console.error("[VoiceSession] Max reconnect attempts reached, stopping radio");
      reconnectAttemptsRef.current = 0;
    }
  }, []);

  const handleAudioStalled = useCallback((_: React.SyntheticEvent<HTMLAudioElement>) => {
    console.log("[VoiceSession] Radio stream stalled, waiting...");
    // Don't immediately reconnect on stalled - browser may recover
  }, []);

  const handleAudioWaiting = useCallback((_: React.SyntheticEvent<HTMLAudioElement>) => {
    console.log("[VoiceSession] Radio stream waiting for data...");
  }, []);

  const handleAudioEnded = useCallback((e: React.SyntheticEvent<HTMLAudioElement>) => {
    const audio = e.currentTarget;
    console.log("[VoiceSession] Radio stream ended");
    // Radio streams shouldn't end - if they do, try to reconnect
    if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS && audio.src) {
      reconnectAttemptsRef.current++;
      console.log(`[VoiceSession] Radio stream ended unexpectedly, reconnecting (attempt ${reconnectAttemptsRef.current}/${MAX_RECONNECT_ATTEMPTS})...`);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = window.setTimeout(() => {
        if (audio.src) {
          audio.load();
          audio.play().catch(err => console.error("[VoiceSession] Reconnect play failed:", err));
        }
      }, RECONNECT_DELAY_MS);
    }
  }, []);

  const handleAudioCanPlay = useCallback((_: React.SyntheticEvent<HTMLAudioElement>) => {
    // Reset reconnect attempts on successful playback
    reconnectAttemptsRef.current = 0;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

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
        <AvatarVisual
          state={state}
          level={mouth.open}
          wide={mouth.wide}
          tier={tier}
          size={size}
        />
      </button>

      <p className="sahaara-avatar__label" role="status" aria-live="polite">
        {label}
      </p>

      {/* Hidden audio element for radio streaming with error handling and auto-reconnect */}
      <audio
        ref={audioRef}
        onError={handleAudioError}
        onStalled={handleAudioStalled}
        onWaiting={handleAudioWaiting}
        onEnded={handleAudioEnded}
        onCanPlay={handleAudioCanPlay}
        // Preload metadata to detect errors faster
        preload="metadata"
        // Explicitly disable looping
        loop={false}
      />

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

// Default export for backward compatibility - uses internal useVoiceSession
export default function SahaaraAvatarDefault({ size = 168 }: { size?: number } = {}) {
  const { state, isActive, toggle, initiativePrompt, dismissInitiative, errorKind, audioRef } =
    useVoiceSession();
  const mouth = useVoiceMouth();
  const { tier } = useVoiceState();

  return (
    <SahaaraAvatar
      size={size}
      state={state}
      isActive={isActive}
      toggle={toggle}
      initiativePrompt={initiativePrompt}
      dismissInitiative={dismissInitiative}
      errorKind={errorKind}
      audioRef={audioRef}
      mouth={mouth}
      tier={tier}
    />
  );
}
