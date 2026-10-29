/**
 * CompanionAlarm.tsx
 *
 * Rings when a timer or reminder fires on the companion channel.
 * Uses the companion channel (no auth required) instead of the caregiver stream.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useCompanionChannel, type CompanionReminderFired } from "./useCompanionChannel";
import { startAlarm, stopAlarm } from "./alarm";
import "./TaskAlarm.css";

const MAX_BANNERS = 3;
const DUPLICATE_WINDOW_MS = 5000;

export default function CompanionAlarm() {
  const [fired, setFired] = useState<CompanionReminderFired[]>([]);
  const [soundBlocked, setSoundBlocked] = useState(false);
  const seenRef = useRef<Map<string, number>>(new Map());
  const channel = useCompanionChannel();

  const dismiss = useCallback((id: string) => {
    setFired((prev) => {
      const next = prev.filter((task) => String(task.id) !== id);
      if (next.length === 0) stopAlarm();
      return next;
    });
  }, []);

  useEffect(() => {
    const event = channel.reminderFired;
    if (!event) return;

    const id = String(event.id);
    const now = Date.now();
    const lastSeen = seenRef.current.get(id);
    if (lastSeen !== undefined && now - lastSeen < DUPLICATE_WINDOW_MS) return;
    seenRef.current.set(id, now);

    const task: CompanionReminderFired = {
      id,
      label: event.label,
      spoken: event.spoken,
      kind: event.kind,
      at: event.at,
    };

    setFired((prev) => [task, ...prev.filter((item) => String(item.id) !== id)].slice(0, MAX_BANNERS));
    const rang = startAlarm();
    setSoundBlocked(!rang);
  }, [channel.reminderFired]);

  // A dismissed banner should not leave the tab silent forever: any tap
  // re-unlocks the audio context for the next timer.
  useEffect(() => {
    if (!soundBlocked) return;
    const unlock = () => {
      setSoundBlocked(false);
      stopAlarm();
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, [soundBlocked]);

  if (fired.length === 0) return null;

  return (
    <div className="task-alarm-stack">
      {fired.map((task) => (
        <section className="task-alarm" role="alert" aria-live="assertive" key={String(task.id)}>
          <span className="task-alarm__badge">{task.label}</span>
          <p className="task-alarm__text">{task.spoken}</p>
          <button type="button" className="task-alarm__stop" onClick={() => dismiss(String(task.id))}>
            Stop
          </button>
        </section>
      ))}
      {soundBlocked && fired.length > 0 && (
        <p className="task-alarm__hint">
          The browser blocked the sound. Tap anywhere once and the next timer will ring.
        </p>
      )}
    </div>
  );
}