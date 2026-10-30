/* oxlint-disable react/only-export-components -- this file intentionally exports hooks beside the provider; the rule only affects dev fast-refresh granularity. */
import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { SILENT_MOUTH, type MouthShape } from "./mouthShape";

// The voice visual that used to be the orb is now the avatar; the state names
// are unchanged so the backend protocol (`{"type":"state","state":"…"}`) needs
// no edits.
export type VoiceStateName = "idle" | "listening" | "thinking" | "speaking" | "error";
export type AvatarTier = "green" | "yellow" | "orange" | "red";

// Three separate contexts on purpose. `mouth` changes on almost every animation
// frame while Sahaara speaks, so bundling it with the state would re-render
// every consumer (the whole caregiver dashboard) ~60 times a second. Actions are
// stable for the provider's lifetime and therefore never re-render anyone.
interface VoiceSnapshot {
  state: VoiceStateName;
  isActive: boolean;
  tier: AvatarTier;
}

interface VoiceActions {
  setState: (state: VoiceStateName) => void;
  setIsActive: (active: boolean) => void;
  setTier: (tier: AvatarTier) => void;
  setMouth: (mouth: MouthShape) => void;
  setListenLevel: (level: number) => void;
}

const VoiceStateContext = createContext<VoiceSnapshot | null>(null);
const VoiceMouthContext = createContext<MouthShape>(SILENT_MOUTH);
const VoiceListenLevelContext = createContext<number>(0);
const VoiceActionsContext = createContext<VoiceActions | null>(null);

export function VoiceStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<VoiceStateName>("idle");
  const [isActive, setIsActive] = useState(false);
  const [tier, setTier] = useState<AvatarTier>("green");
  const [mouth, setMouth] = useState<MouthShape>(SILENT_MOUTH);
  const [listenLevel, setListenLevel] = useState(0);

  const snapshot = useMemo<VoiceSnapshot>(
    () => ({ state, isActive, tier }),
    [state, isActive, tier],
  );

  const actions = useMemo<VoiceActions>(
    () => ({ setState, setIsActive, setTier, setMouth, setListenLevel }),
    [],
  );

  return (
    <VoiceActionsContext.Provider value={actions}>
      <VoiceStateContext.Provider value={snapshot}>
        <VoiceMouthContext.Provider value={mouth}>
          <VoiceListenLevelContext.Provider value={listenLevel}>
            {children}
          </VoiceListenLevelContext.Provider>
        </VoiceMouthContext.Provider>
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

/** Live mouth shape from the TTS playback. Updates on almost every frame. */
export function useVoiceMouth(): MouthShape {
  return useContext(VoiceMouthContext);
}

/** Live microphone (input) level for the listening ring. Updates while she is
 *  listening; 0 otherwise. Its own context so only the ring re-renders. */
export function useListenLevel(): number {
  return useContext(VoiceListenLevelContext);
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
