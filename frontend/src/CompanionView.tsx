import { useEffect, useState } from "react";

import ErrorBoundary from "./ErrorBoundary";
import SahaaraAvatar from "./SahaaraAvatar";
import { useCompanionChannel } from "./useCompanionChannel";
import { useVoiceSession } from "./useVoiceSession";
import { VoiceStateProvider } from "./VoiceStateContext";
import "./CompanionView.css";

function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/**
 * Plain voice control for the rare case the avatar's render throws. Kept here
 * rather than imported from the caregiver shell, so this page keeps its "no
 * caregiver module" guarantee even on the failure path.
 */
function CompanionVoiceFallback() {
  const { state, isActive, toggle } = useVoiceSession();
  return (
    <div className="companion-voice-fallback">
      <p>Sahaara's picture had a problem, but you can still talk.</p>
      <button type="button" onClick={toggle}>
        {isActive ? "Stop talking" : "Start talking"}
      </button>
      <p>{isActive ? state : "Voice is off"}</p>
    </div>
  );
}

/**
 * The person's screen: full screen, no login, no tabs, no caregiver data.
 *
 * It subscribes to `/stream/ws/companion` only. The clock is the device's real
 * clock; the caption is the last reply Sahaara actually spoke. The richer
 * activity strip (now playing, reminder countdown, next routine) is built in
 * Step 6 from the same channel.
 */
export default function CompanionView() {
  const now = useNow();
  const channel = useCompanionChannel();

  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const date = now.toLocaleDateString([], {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <div className="companion-shell">
      <h1 className="companion-sr-title">Sahaara</h1>

      <header className="companion-clock" aria-label="Time and date">
        <p className="companion-time">{time}</p>
        <p className="companion-date">{date}</p>
      </header>

      <main className="companion-main">
        <ErrorBoundary name="SahaaraAvatar" fallback={<CompanionVoiceFallback />}>
          <VoiceStateProvider>
            <SahaaraAvatar size={240} />
          </VoiceStateProvider>
        </ErrorBoundary>

        {channel.caption && (
          <p className="companion-caption" role="status" aria-live="polite">
            {channel.caption.text}
          </p>
        )}
      </main>
    </div>
  );
}
