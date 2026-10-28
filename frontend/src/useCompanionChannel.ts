import { useEffect, useState } from "react";

import { streamWsUrl } from "./api";
import { parseCompanionMessage, type CompanionMessage } from "./companionChannel";

export interface CompanionCaption {
  text: string;
  at: number | null;
}

export interface CompanionReminder {
  id: number;
  label: string;
  fireAt: number;
  secondsRemaining: number;
}

export interface CompanionRoutine {
  label: string;
  time: string;
  day: "today" | "tomorrow";
}

export interface CompanionNowPlaying {
  title: string;
  kind: "song" | "radio";
}

export interface CompanionChannelState {
  caption: CompanionCaption | null;
  clock: { now: number; date: string } | null;
  reminder: CompanionReminder | null;
  routine: CompanionRoutine | null;
  nowPlaying: CompanionNowPlaying | null;
}

export const EMPTY_COMPANION_STATE: CompanionChannelState = {
  caption: null,
  clock: null,
  reminder: null,
  routine: null,
  nowPlaying: null,
};

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Pure state transition, kept here so the socket effect stays small. */
export function applyCompanionMessage(
  state: CompanionChannelState,
  message: CompanionMessage,
): CompanionChannelState {
  switch (message.type) {
    case "caption":
      return {
        ...state,
        caption: {
          text: String(message.data.text),
          at: numberOrNull(message.data.at),
        },
      };
    case "clock":
      return {
        ...state,
        clock: {
          now: Number(message.data.now),
          date: String(message.data.date),
        },
      };
    case "reminder_countdown":
      if (message.data.present === false) {
        return { ...state, reminder: null };
      }
      return {
        ...state,
        reminder: {
          id: Number(message.data.id),
          label: String(message.data.label ?? "Reminder"),
          fireAt: Number(message.data.fire_at),
          secondsRemaining: Number(message.data.seconds_remaining),
        },
      };
    case "next_routine":
      return {
        ...state,
        routine: {
          label: String(message.data.label),
          time: String(message.data.time),
          day: message.data.day === "tomorrow" ? "tomorrow" : "today",
        },
      };
    case "now_playing":
      if (message.data.playing !== true) {
        return { ...state, nowPlaying: null };
      }
      return {
        ...state,
        nowPlaying: {
          title: String(message.data.title),
          kind: message.data.kind === "radio" ? "radio" : "song",
        },
      };
    default:
      return state;
  }
}

const RECONNECT_MS = 3000;

/**
 * Subscribe to the companion channel — and to nothing else.
 *
 * Deliberately not `eventStream.ts`: that socket carries the caregiver feed.
 * This hook only knows `/stream/ws/companion` and only applies the five
 * whitelisted kinds, so the person's screen has no code path to a tier or an
 * alert even if someone later adds a button to it.
 */
export function useCompanionChannel(): CompanionChannelState {
  const [state, setState] = useState<CompanionChannelState>(EMPTY_COMPANION_STATE);

  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let start: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      socket = new WebSocket(streamWsUrl("/stream/ws/companion"));

      socket.onmessage = (event: MessageEvent) => {
        const message = parseCompanionMessage(event.data);
        if (!message) return;
        setState((current) => applyCompanionMessage(current, message));
      };

      socket.onerror = () => {
        socket?.close();
      };

      socket.onclose = () => {
        if (closed) return;
        retry = setTimeout(connect, RECONNECT_MS);
      };
    };

    // Defer one tick so React StrictMode's mount/cleanup/mount in development
    // never opens (and immediately aborts) a socket on the first pass.
    start = setTimeout(connect, 0);

    return () => {
      closed = true;
      if (start) clearTimeout(start);
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return state;
}
