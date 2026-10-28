/**
 * eventStream.ts
 *
 * One connection to `/stream/ws/events`, shared by every component that needs
 * live dashboard events (the caregiver timeline and the task alarm).
 *
 * Why this exists: the connection used to live inside EscalationLadder, so it
 * died whenever the caregiver switched tabs, and it never came back after the
 * backend restarted (`uvicorn --reload` does this on every code save, which is
 * also where the Vite `ws proxy socket error: EPIPE` lines came from). Now
 * anybody can subscribe, the socket reconnects with backoff, and a second
 * subscriber reuses the same socket instead of opening another.
 *
 * The socket is same-origin and authenticates with the session cookie — no
 * token in the URL.
 */
import { openWebSocket } from "./api";

export interface EventEnvelope {
  type: string;
  data: unknown;
}

type EventListener = (message: EventEnvelope) => void;
type ConnectionListener = (connected: boolean) => void;

const listeners = new Set<EventListener>();
const connectionListeners = new Set<ConnectionListener>();

let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let attempts = 0;
let connected = false;
// Set when the last subscriber leaves, so a deliberate close cannot schedule a
// reconnect after the component is gone.
let stopped = true;
// A rejected handshake (403/1008) will not fix itself by retrying every second.
// Back off hard instead of hammering a server that is refusing us.
let authRejected = false;

const MAX_BACKOFF_MS = 5000;

function setConnected(next: boolean): void {
  if (connected === next) return;
  connected = next;
  connectionListeners.forEach((listener) => {
    try {
      listener(next);
    } catch {
      /* one bad listener must not break the rest */
    }
  });
}

function scheduleReconnect(): void {
  if (stopped || reconnectTimer !== null || listeners.size === 0) return;
  const delay = authRejected
    ? Math.min(30000, 5000 * 2 ** Math.min(attempts, 3))
    : Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempts);
  attempts += 1;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function connect(): void {
  if (socket || listeners.size === 0 || stopped) return;

  let ws: WebSocket;
  try {
    ws = openWebSocket("/stream/ws/events");
  } catch {
    scheduleReconnect();
    return;
  }
  socket = ws;

  ws.onopen = () => {
    attempts = 0;
    authRejected = false;
    setConnected(true);
  };

  ws.onmessage = (event) => {
    let message: EventEnvelope | null = null;
    try {
      message = JSON.parse(String(event.data)) as EventEnvelope;
    } catch {
      return;
    }
    if (!message || typeof message.type !== "string") return;
    // Copy first: a listener may unsubscribe while we are iterating.
    [...listeners].forEach((listener) => {
      try {
        listener(message as EventEnvelope);
      } catch {
        /* a broken consumer must not take the stream down */
      }
    });
  };

  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      /* already closed */
    }
  };

  ws.onclose = (event) => {
    if (socket === ws) socket = null;
    setConnected(false);
    // 1008 = policy violation: the backend refused the session. Retrying fast
    // would just spin; wait for a real login to re-subscribe.
    if (event.code === 1008 || event.code === 4401 || event.code === 4403) {
      authRejected = true;
    }
    scheduleReconnect();
  };
}

/**
 * Subscribe to live events. Returns an unsubscribe function; the socket closes
 * when the last subscriber leaves.
 */
export function subscribeEvents(listener: EventListener): () => void {
  listeners.add(listener);
  stopped = false;
  connect();

  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    stopped = true;
    authRejected = false;
    attempts = 0;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      const closing = socket;
      socket = null;
      try {
        closing.close();
      } catch {
        /* already closed */
      }
    }
    setConnected(false);
  };
}

/** Subscribe to connection state. Fires immediately with the current value. */
export function subscribeConnection(listener: ConnectionListener): () => void {
  connectionListeners.add(listener);
  try {
    listener(connected);
  } catch {
    /* ignore */
  }
  return () => {
    connectionListeners.delete(listener);
  };
}
