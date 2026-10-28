/**
 * The only five kinds the companion screen is allowed to understand.
 *
 * This mirrors `backend/dashboard/companion.py`. The backend already refuses
 * anything else, but the client repeats the allowlist so a future proxy,
 * cached frame, or accidental second publisher still cannot put a tier, an
 * alert or quoted speech on this screen. Unknown keys are dropped; a key that
 * smells like caregiver data rejects the whole message.
 */
export type CompanionKind =
  | "caption"
  | "clock"
  | "now_playing"
  | "reminder_countdown"
  | "next_routine";

export interface CompanionMessage {
  type: CompanionKind;
  data: Record<string, unknown>;
}

export const COMPANION_KINDS: readonly CompanionKind[] = [
  "caption",
  "clock",
  "now_playing",
  "reminder_countdown",
  "next_routine",
];

const ALLOWED_KEYS: Record<CompanionKind, ReadonlySet<string>> = {
  caption: new Set(["text", "at"]),
  clock: new Set(["now", "iso", "date"]),
  now_playing: new Set(["title", "kind", "playing", "at"]),
  reminder_countdown: new Set([
    "present",
    "id",
    "label",
    "fire_at",
    "seconds_remaining",
    "at",
  ]),
  next_routine: new Set(["label", "time", "day"]),
};

export const FORBIDDEN_COMPANION_KEY =
  /tier|alert|consent|danger|symptom|emotion|severity|distress|repeat|fallback|physical|diagnos/i;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * Parse one text frame. Returns null for malformed JSON, an unknown kind, a
 * forbidden key, or a payload missing the fields its kind needs. Callers never
 * see a partially trusted object.
 */
export function parseCompanionMessage(raw: unknown): CompanionMessage | null {
  if (typeof raw !== "string") return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;

  const envelope = parsed as { type?: unknown; data?: unknown };
  if (typeof envelope.type !== "string") return null;
  if (!COMPANION_KINDS.includes(envelope.type as CompanionKind)) return null;

  const kind = envelope.type as CompanionKind;
  const data = envelope.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;

  const clean: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    if (FORBIDDEN_COMPANION_KEY.test(key)) return null;
    if (ALLOWED_KEYS[kind].has(key)) clean[key] = value;
  }

  switch (kind) {
    case "caption":
      if (!isNonEmptyString(clean.text)) return null;
      break;
    case "clock":
      if (!isFiniteNumber(clean.now)) return null;
      if (typeof clean.date !== "string") return null;
      break;
    case "now_playing":
      if (!isNonEmptyString(clean.title)) return null;
      if (clean.kind !== "song" && clean.kind !== "radio") return null;
      if (typeof clean.playing !== "boolean") return null;
      break;
    case "reminder_countdown":
      if (typeof clean.present !== "boolean") return null;
      if (clean.present) {
        if (!isFiniteNumber(clean.id)) return null;
        if (!isFiniteNumber(clean.fire_at)) return null;
        if (!isFiniteNumber(clean.seconds_remaining)) return null;
      }
      break;
    case "next_routine":
      if (!isNonEmptyString(clean.label)) return null;
      if (!isNonEmptyString(clean.time)) return null;
      if (clean.day !== "today" && clean.day !== "tomorrow") return null;
      break;
  }

  return { type: kind, data: clean };
}
