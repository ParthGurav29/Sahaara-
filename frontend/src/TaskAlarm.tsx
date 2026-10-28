/**
 * TaskAlarm.tsx
 *
 * Rings when a timer or reminder actually fires, and shows the line the
 * caregiver dashboard records.
 *
 * This is deliberately independent of the voice session: on 2026-10-28 Asha's
 * 5-second timer fired nothing at all, and even after that is fixed a timer
 * would stay silent whenever the avatar session was closed. The dashboard
 * event stream is the one channel that is always open while the app is, so the
 * chime lives here. If a voice session *is* open the backend also speaks the
 * line — the same sentence, never a second voice.
 *
 * Mounted once in App.tsx so it keeps working on every tab.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { reminderBannerLabel, reminderBannerText, startAlarm, stopAlarm } from "./alarm";
import { subscribeEvents } from "./eventStream";
import "./TaskAlarm.css";

interface FiredTask {
  id: string;
  text: string;
  label: string;
  at: number;
}

interface ReminderPayload {
  reminder_id?: unknown;
  kind?: unknown;
  label?: unknown;
  spoken?: unknown;
  timestamp_iso?: unknown;
}

const MAX_BANNERS = 3;
// The same fire can reach us twice (the event log plus a scheduler initiative);
// one chime per reminder id inside this window is enough.
const DUPLICATE_WINDOW_MS = 5000;

export default function TaskAlarm() {
  const [fired, setFired] = useState<FiredTask[]>([]);
  const [soundBlocked, setSoundBlocked] = useState(false);
  const seenRef = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    setFired((prev) => {
      const next = prev.filter((task) => task.id !== id);
      if (next.length === 0) stopAlarm();
      return next;
    });
  }, []);

  useEffect(() => {
    const unsubscribe = subscribeEvents((message) => {
      if (message.type !== "event") return;
      const event = message.data as {
        type?: string;
        detail?: ReminderPayload;
        timestamp?: number;
        timestamp_iso?: string;
      };
      if (!event || event.type !== "reminder_fired") return;

      const detail = event.detail ?? {};
      const id = String(detail.reminder_id ?? event.timestamp_iso ?? Date.now());
      const now = Date.now();
      const lastSeen = seenRef.current.get(id);
      if (lastSeen !== undefined && now - lastSeen < DUPLICATE_WINDOW_MS) return;
      seenRef.current.set(id, now);

      const task: FiredTask = {
        id,
        text: reminderBannerText(detail),
        label: reminderBannerLabel(detail),
        at: typeof event.timestamp === "number" ? event.timestamp : now / 1000,
      };

      setFired((prev) => [task, ...prev.filter((item) => item.id !== id)].slice(0, MAX_BANNERS));
      const rang = startAlarm();
      setSoundBlocked(!rang);
    });

    return () => {
      unsubscribe();
      stopAlarm();
    };
  }, []);

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
        <section className="task-alarm" role="alert" aria-live="assertive" key={task.id}>
          <span className="task-alarm__badge">{task.label}</span>
          <p className="task-alarm__text">{task.text}</p>
          <button type="button" className="task-alarm__stop" onClick={() => dismiss(task.id)}>
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
