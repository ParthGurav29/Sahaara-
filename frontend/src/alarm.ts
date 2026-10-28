/**
 * alarm.ts
 *
 * The chime that rings when a timer or reminder actually fires.
 *
 * Why it exists (2026-10-28): Sahaara said "I've set a timer for 5 seconds",
 * and when the time came nothing rang — with no voice session open the event
 * only reached the dashboard. A timer the person asked for has to make a
 * sound, so the browser rings whether or not the avatar is listening.
 *
 * Rules kept:
 *  - Calm, not alarming. A soft two-tone bell, three times, ~8 seconds. No
 *    klaxon, no flashing, no escalation-tier colours.
 *  - Generated with WebAudio, so there is no audio asset to ship or license.
 *  - Nothing pretends: if the browser blocks audio before any interaction,
 *    `startAlarm()` reports false and the caller says so in the banner.
 */

export interface AlarmTone {
  /** Milliseconds after the alarm starts. */
  atMs: number;
  freqHz: number;
  durationMs: number;
  /** Peak gain, 0–1. Kept low on purpose. */
  gain: number;
}

/** Three gentle rings. */
export const ALARM_RING_OFFSETS_MS = [0, 2500, 5000];

const RING_TONES = [
  { offsetMs: 0, freqHz: 880, durationMs: 380, gain: 0.22 },
  { offsetMs: 420, freqHz: 660, durationMs: 620, gain: 0.18 },
];

/** When the last tone ends. The alarm stops itself after the tail. */
export const ALARM_TOTAL_MS = 5000 + 420 + 620 + 400;

/** Flattened, absolute-time schedule. Pure, so it can be tested. */
export function alarmSchedule(): AlarmTone[] {
  const tones: AlarmTone[] = [];
  for (const ring of ALARM_RING_OFFSETS_MS) {
    for (const tone of RING_TONES) {
      tones.push({
        atMs: ring + tone.offsetMs,
        freqHz: tone.freqHz,
        durationMs: tone.durationMs,
        gain: tone.gain,
      });
    }
  }
  return tones;
}

export interface ReminderDetail {
  spoken?: unknown;
  kind?: unknown;
  label?: unknown;
}

/** The line shown in the banner. Never invents a timer that did not fire. */
export function reminderBannerText(detail: ReminderDetail | null | undefined): string {
  const spoken = typeof detail?.spoken === "string" ? detail.spoken.trim() : "";
  if (spoken) return spoken;
  if (detail?.kind === "reminder") return "Your reminder is due.";
  return "Your timer is done.";
}

/** Short heading for the banner. */
export function reminderBannerLabel(detail: ReminderDetail | null | undefined): string {
  return detail?.kind === "reminder" ? "Reminder" : "Timer finished";
}

// ---------------------------------------------------------------------------
// Browser playback
// ---------------------------------------------------------------------------

let context: AudioContext | null = null;
let playing: OscillatorNode[] = [];
let stopTimer: number | null = null;

type AudioContextCtor = typeof AudioContext;

function audioContextCtor(): AudioContextCtor | null {
  if (typeof window === "undefined") return null;
  const candidate =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
  return candidate ?? null;
}

/**
 * Create/resume the audio context on a real user gesture. Call this from the
 * first click/keypress so the chime is allowed to play later, when nobody is
 * touching the screen.
 */
export function unlockAlarmAudio(): boolean {
  const Ctor = audioContextCtor();
  if (!Ctor) return false;
  if (!context) {
    try {
      context = new Ctor();
    } catch {
      return false;
    }
  }
  if (context.state === "suspended") {
    void context.resume().catch(() => {});
  }
  return context.state !== "closed";
}

/** True while the chime is scheduled or still ringing. */
export function isAlarmPlaying(): boolean {
  return playing.length > 0;
}

/**
 * Ring the timer chime. Returns false when the browser has no usable audio
 * context (no WebAudio, or autoplay blocked and never unlocked) — the caller
 * keeps the visual banner either way.
 */
export function startAlarm(): boolean {
  if (!unlockAlarmAudio() || !context) return false;

  stopAlarm();

  const ctx = context;
  const now = ctx.currentTime;

  try {
    for (const tone of alarmSchedule()) {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = tone.freqHz;

      const start = now + tone.atMs / 1000;
      const end = start + tone.durationMs / 1000;
      // Short attack then a smooth decay — a bell, not a beep.
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(tone.gain, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);

      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(start);
      oscillator.stop(end + 0.05);
      playing.push(oscillator);
    }
  } catch {
    stopAlarm();
    return false;
  }

  stopTimer = window.setTimeout(() => {
    stopTimer = null;
    stopAlarm();
  }, ALARM_TOTAL_MS);

  return true;
}

/** Stop the chime immediately (the banner stays until dismissed). */
export function stopAlarm(): void {
  if (stopTimer !== null) {
    window.clearTimeout(stopTimer);
    stopTimer = null;
  }
  for (const oscillator of playing) {
    try {
      oscillator.stop();
      oscillator.disconnect();
    } catch {
      /* already stopped */
    }
  }
  playing = [];
}
