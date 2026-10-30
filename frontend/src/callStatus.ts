/**
 * callStatus.ts
 *
 * Honest one-line descriptions of an emergency call attempt, for the caregiver
 * dashboard (Emergency Call Task 4).
 *
 * The app can only ever know what the provider reported. It cannot know whether
 * a human actually heard the message, pressed 1, or was awake. Every label below
 * says exactly what the provider reported and nothing more.
 *
 * `tests/callStatus.test.ts` forbids the words:
 * "delivered", "delivery", "displayed", "seen", "read", "received by",
 * "on the way", "coming", "ambulance", "help is coming", "safe", "detected".
 *
 * Pure module (no React, no browser globals) so `node --test` can import it.
 */

export type CallTone = "neutral" | "muted" | "warn" | "urgent";

export interface CallStatus {
  // Per-attempt: queued | dialing | ringing | answered | answered_unacknowledged
  //               | acknowledged | no_answer | failed | canceled
  status?: string;
  // Incident-level: cooldown | no_callable_contact | disabled | no_caregiver_reached
  //                 | all_contacts_exhausted | max_attempts_reached | caregiver_cancelled | etc.
  reason?: string;
  simulated?: boolean;      // true for SimulatedCallProvider
  contact_name?: string;    // caregiver name
  phone_masked?: string;    // masked phone number
  attempt?: number;         // attempt number (1..4)
  incident_status?: string; // overall incident status
}

export interface CallDescription {
  label: string;
  tone: CallTone;
}

/** What the provider answered, phrased without claiming a human received it. */
export function describeCallStatus(
  call: CallStatus | null | undefined,
): CallDescription | null {
  if (!call) return null;

  // When the caller does not know who the attempt was for, the label must not
  // invent a name — the timeline only carries the status, not the contact.
  const described = describeNonSimulated(
    (call.status || "").toLowerCase(),
    (call.reason || "").toLowerCase(),
    call.contact_name || "",
    call.phone_masked || "",
    call.incident_status,
  );

  // A simulated call must always say so — and must still say what the provider
  // reported. Returning only "SIMULATED" hid every status, so a ladder of three
  // attempts all read identically and a caregiver could not tell a no-answer
  // from a confirmed call.
  if (call.simulated === true) {
    return { label: `${described.label} · simulated`, tone: described.tone };
  }
  return described;
}

function describeNonSimulated(
  status: string,
  reason: string,
  contact: string,
  masked: string,
  incidentStatus?: string,
): CallDescription {
  // Incident-level terminal states win over the per-attempt status: "the whole
  // call-out ended" is what the caregiver needs when it is over.
  if (incidentStatus) {
    const incStatus = incidentStatus.toLowerCase();
    if (incStatus === "exhausted") {
      // The ladder ran to the end. "No caregiver reached" is the reason the
      // orchestrator records; the label says what happened without implying
      // anyone was notified.
      if (reason === "no_caregiver_reached" || reason === "all_contacts_exhausted") {
        return { label: "No caregiver was reached — dashboard alert only", tone: "warn" };
      }
      return { label: "No contact acknowledged a call — dashboard alert only", tone: "warn" };
    }
    if (incStatus === "cancelled" || incStatus === "canceled") {
      return { label: "Call campaign cancelled by caregiver", tone: "neutral" };
    }
    if (incStatus === "acknowledged") {
      return {
        label: contact
          ? `${contact} pressed 1 to acknowledge`
          : "Caller pressed 1 to acknowledge",
        tone: "neutral",
      };
    }
    if (incStatus === "failed") {
      switch (reason) {
        case "disabled":
          return { label: "Emergency calling is turned off — dashboard alert only", tone: "muted" };
        case "no_callable_contact":
          return {
            label: masked
              ? `No callable contact for ${contact} — no call was placed`
              : `No contact has both a number and permission — no call was placed`,
            tone: "muted",
          };
        case "cooldown":
          return { label: "No new call — a recent emergency call was already placed", tone: "muted" };
        case "no_caregiver_reached":
          return { label: "No caregiver was reached — dashboard alert only", tone: "warn" };
        default:
          return { label: "Call could not be placed — dashboard alert only", tone: "warn" };
      }
    }
  }

  // Per-attempt states
  switch (status) {
    case "queued":
      return { label: contact ? `Call to ${contact} queued` : "Call queued", tone: "neutral" };
    case "dialing":
      return { label: contact ? `Calling ${contact}…` : "Calling…", tone: "neutral" };
    case "ringing":
      return { label: contact ? `Ringing ${contact}` : "Ringing", tone: "neutral" };
    case "answered":
    case "answered_unacknowledged":
      // The carrier says the line opened; that is not a person acknowledging.
      return {
        label: contact
          ? `${contact}'s phone answered — confirmation not received`
          : "Phone answered — confirmation not received",
        tone: "neutral",
      };
    case "acknowledged":
      // DTMF 1 received by the provider.
      return {
        label: contact ? `${contact} pressed 1 to acknowledge` : "Caller pressed 1 to acknowledge",
        tone: "neutral",
      };
    case "no_answer":
      // The provider's `no_answer` covers "answered but no keypress" and "never
      // answered". One sentence is true in both cases.
      return { label: "No acknowledgement from the call", tone: "warn" };
    case "canceled":
    case "cancelled":
      return { label: contact ? `Call to ${contact} cancelled` : "Call cancelled", tone: "muted" };
    case "failed":
      return { label: contact ? `Call to ${contact} failed` : "Call failed", tone: "warn" };
    default:
      return { label: "Call status unknown", tone: "warn" };
  }
}

/** Describe the next-contact transition */
export function describeNextContact(
  nextContactName: string,
): CallDescription {
  return {
    label: `Trying ${nextContactName} next`,
    tone: "warn",
  };
}

/** Forbidden words test helper */
export const FORBIDDEN_WORDS = [
  "delivered",
  "delivery",
  "displayed",
  "seen",
  "read",
  "received by",
  "on the way",
  "coming",
  "ambulance",
  "help is coming",
  "safe",
  "detected",
];
