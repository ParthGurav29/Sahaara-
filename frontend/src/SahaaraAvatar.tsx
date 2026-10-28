import { useEffect, useRef, useState } from "react";
import "./SahaaraAvatar.css";

export type AvatarState = "idle" | "listening" | "thinking" | "speaking" | "error";
export type AvatarTier = "green" | "yellow" | "orange" | "red";

interface SahaaraAvatarProps {
  state: AvatarState;
  level?: number;          // 0–1 audio amplitude (speaking only)
  tier?: AvatarTier;       // defaults to 'green'
}

const TIER_SPEED: Record<AvatarTier, number> = {
  green: 1.0,
  yellow: 0.8,
  orange: 0.6,
  red: 0.4,
};

const TIER_GLOW: Record<AvatarTier, number> = {
  green: 0.40,
  yellow: 0.30,
  orange: 0.20,
  red: 0.15,
};

const TIER_MOUTH: Record<AvatarTier, number> = {
  green: 1.0,
  yellow: 0.9,
  orange: 0.7,
  red: 0.5,
};

const STATE_COLOR: Record<AvatarState, string> = {
  idle: "#C9C2B8",
  listening: "#7C93A8",
  thinking: "#A98FC7",
  speaking: "#E8A85C",
  error: "#C2716B",
};

// Mouth geometry, in the 130×130 viewBox units. The mouth line is 46 units
// long (x1=42 → x2=88); MOUTH_REST keeps a short calm line when silent and
// MOUTH_FULL is the widest it can open.
const MOUTH_PATH_LENGTH = 46;
const MOUTH_REST = 10;
const MOUTH_FULL = 46;

export default function SahaaraAvatar({ state, level = 0, tier = "green" }: SahaaraAvatarProps) {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const mouthRef = useRef<SVGLineElement>(null);
  const leftEyeRef = useRef<SVGCircleElement>(null);
  const rightEyeRef = useRef<SVGCircleElement>(null);
  const orbRef = useRef<SVGCircleElement>(null);
  const glowRef = useRef<SVGCircleElement>(null);
  const tiltRef = useRef<SVGGElement>(null);

  const animSpeed = TIER_SPEED[tier];
  const glowOpacity = TIER_GLOW[tier];
  const mouthScale = TIER_MOUTH[tier];

  // Reduced motion detection
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setPrefersReducedMotion(mediaQuery.matches);
    const handler = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);

  // Apply CSS custom properties for animations
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
    root.setProperty("--mouth-dash", String(MOUTH_REST + (MOUTH_FULL - MOUTH_REST) * clamped * mouthScale));
    // Eyes read as a soft resting face when idle, brighter when engaged.
    const isActiveState = state !== "idle";
    root.setProperty("--eye-opacity", String(isActiveState ? 0.75 : 0.35));
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
      viewBox="0 0 130 130"
      width="130"
      height="130"
      role="img"
      aria-label={`Sahaara is ${state}`}
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
        ref={glowRef}
        className="avatar-glow"
        cx="65"
        cy="65"
        r="58"
        fill="url(#outerGlow)"
        style={{ opacity: prefersReducedMotion ? 0 : 1 }}
      />

      {/* Main orb with tilt group */}
      <g ref={tiltRef} className="avatar-orb-group">
        <circle
          ref={orbRef}
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
          ref={leftEyeRef}
          className="avatar-eye"
          cx="40"
          cy="45"
          r="5"
          fill="url(#eyeGlow)"
          style={{ opacity: "var(--eye-opacity)" }}
        />
        <circle
          ref={rightEyeRef}
          className="avatar-eye"
          cx="90"
          cy="45"
          r="5"
          fill="url(#eyeGlow)"
          style={{ opacity: "var(--eye-opacity)" }}
        />

        {/* Mouth — a calm line that opens symmetrically with audio level */}
        <line
          ref={mouthRef}
          className="avatar-mouth"
          x1="42"
          y1="85"
          x2="88"
          y2="85"
          stroke="var(--orb-color)"
          strokeWidth="2.5"
          strokeLinecap="round"
          style={{
            strokeDasharray: "var(--mouth-dash) 46",
            // Centres the dash: the visible run starts at (46 - dash) / 2.
            strokeDashoffset: `calc(var(--mouth-dash) * 0.5 - ${MOUTH_PATH_LENGTH / 2})`,
            opacity: state === "speaking" ? 1 : 0.45,
          }}
        />
      </g>

      {/* Reduced motion fallback: color-only indicator */}
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
          strokeDashoffset={state === "idle" ? 345 : state === "listening" ? 200 : state === "thinking" ? 100 : 0}
        />
      )}
    </svg>
  );
}