/* oxlint-disable react/only-export-components -- this file intentionally exports hooks beside the provider; the rule only affects dev fast-refresh granularity. */
import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "error";
export type AvatarTier = "green" | "yellow" | "orange" | "red";

// Three separate contexts on purpose. `level` changes on every animation frame
// while Sahaara speaks, so bundling it with the state would re-render every
// consumer (the whole caregiver dashboard) ~60 times a second. Actions are
// stable for the provider's lifetime and therefore never re-render anyone.
interface VoiceSnapshot {
  state: OrbState;
  isActive: boolean;
  tier: AvatarTier;
}

interface VoiceActions {
  setState: (state: OrbState) => void;
  setIsActive: (active: boolean) => void;
  setTier: (tier: AvatarTier) => void;
  setLevel: (level: number) => void;
}

const VoiceStateContext = createContext<VoiceSnapshot | null>(null);
const VoiceLevelContext = createContext<number>(0);
const VoiceActionsContext = createContext<VoiceActions | null>(null);

export function VoiceStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OrbState>("idle");
  const [isActive, setIsActive] = useState(false);
  const [tier, setTier] = useState<AvatarTier>("green");
  const [level, setLevel] = useState(0);

  const snapshot = useMemo<VoiceSnapshot>(
    () => ({ state, isActive, tier }),
    [state, isActive, tier],
  );

  const actions = useMemo<VoiceActions>(
    () => ({ setState, setIsActive, setTier, setLevel }),
    [],
  );

  return (
    <VoiceActionsContext.Provider value={actions}>
      <VoiceStateContext.Provider value={snapshot}>
        <VoiceLevelContext.Provider value={level}>
          {children}
        </VoiceLevelContext.Provider>
      </VoiceStateContext.Provider>
    </VoiceActionsContext.Provider>
  );
}

/** Stable setters. Safe to depend on in effects; never causes a re-render. */
export function useVoiceActions(): VoiceActions {
  const context = useContext(VoiceActionsContext);
  if (!context) {
    throw new Error("useVoiceActions must be used within a VoiceStateProvider");
  }
  return context;
}

/** Playback amplitude, 0–1. Updates every frame while audio plays. */
export function useVoiceLevel(): number {
  return useContext(VoiceLevelContext);
}

/** State + tier. Changes only when Sahaara's state or the tier actually moves. */
export function useVoiceState(): VoiceSnapshot {
  const context = useContext(VoiceStateContext);
  if (!context) {
    throw new Error("useVoiceState must be used within a VoiceStateProvider");
  }
  return context;
}

/** Never throws — for components that may render outside the provider. */
export function useVoiceStateReadOnly(): VoiceSnapshot {
  const context = useContext(VoiceStateContext);
  return context ?? { state: "idle", isActive: false, tier: "green" };
}
