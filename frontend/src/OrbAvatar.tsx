import { useEffect, useRef, useState } from "react";
import { useVoiceStateReadOnly } from "./VoiceStateContext";
import "./OrbAvatar.css";

type OrbState = "idle" | "listening" | "thinking" | "speaking";

interface OrbAvatarProps {
  size?: number;
  onToggle: () => void;
  isActive: boolean;
  disabled?: boolean;
}

export default function OrbAvatar({ size = 280, onToggle, isActive, disabled }: OrbAvatarProps) {
  const { state } = useVoiceStateReadOnly();
  const [orbState, setOrbState] = useState<OrbState>("idle");
  const [mouthOpen, setMouthOpen] = useState(false);
  const animationRef = useRef<number | null>(null);

  // Sync with voice session state
  useEffect(() => {
    const mappedState: OrbState = state === "error" ? "idle" : state;
    if (mappedState !== orbState) {
      setOrbState(mappedState);
    }
  }, [state, orbState]);

  // Mouth animation for speaking state
  useEffect(() => {
    if (orbState === "speaking") {
      const animate = () => {
        setMouthOpen((prev) => !prev);
        animationRef.current = requestAnimationFrame(animate);
      };
      animationRef.current = requestAnimationFrame(animate);
    } else {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
      setMouthOpen(false);
    }
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [orbState]);

  return (
    <button
      className={`orbbtn ${orbState}`}
      onClick={disabled ? undefined : onToggle}
      disabled={disabled}
      aria-label={isActive ? "Stop talking to Sahaara" : "Tap to talk to Sahaara"}
      style={{ width: size, height: size } as React.CSSProperties}
    >
      <span className="ring" aria-hidden="true" />
      <span className="orb" aria-hidden="true" />
      <span className="face" aria-hidden="true">
        <i className="eye l" />
        <i className="eye r" />
        <i className={`mouth ${mouthOpen ? "open" : ""}`} />
      </span>
    </button>
  );
}