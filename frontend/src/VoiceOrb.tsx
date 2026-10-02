import { useCallback, useEffect, useRef, useState } from "react";
import "./VoiceOrb.css";

type OrbState = "idle" | "listening" | "thinking" | "speaking" | "error";

const STATE_LABEL: Record<OrbState, string> = {
  idle: "Tap to talk",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Connection lost — tap to retry",
};

// Deliberately not the generic AI palette (no near-black + neon, no warm
// terracotta) — soft, low-arousal colors suited to a calm companion app
// for someone who may be anxious or easily overstimulated.
const STATE_COLOR: Record<OrbState, string> = {
  idle: "#C9C2B8", // neutral stone — nothing is happening yet
  listening: "#7C93A8", // slate blue — attentive
  thinking: "#A98FC7", // muted lavender — working on it
  speaking: "#E8A85C", // warm amber — present, speaking
  error: "#C2716B", // muted clay-red — something's wrong, not alarming
};

const WS_URL =
  (import.meta as any).env?.VITE_VOICE_WS_URL ?? "ws://localhost:8000/ws/voice";

// Must match voice/asr.py's Deepgram config (linear16, 16000Hz, mono).
const MIC_SAMPLE_RATE = 16000;
// Must match voice/tts.py's Cartesia output_format (pcm_s16le, 22050Hz, mono).
const PLAYBACK_SAMPLE_RATE = 22050;

function floatTo16BitPCM(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output;
}

/** Naive decimation resampler. The mic's native rate is whatever the
 * browser gives us (usually 44.1k/48k) — good enough quality for speech
 * recognition; swap for a proper polyphase resampler if transcription
 * accuracy turns out to suffer. */
function downsampleTo16k(input: Float32Array, inputSampleRate: number): Int16Array {
  if (inputSampleRate === MIC_SAMPLE_RATE) return floatTo16BitPCM(input);
  const ratio = inputSampleRate / MIC_SAMPLE_RATE;
  const outLength = Math.floor(input.length / ratio);
  const result = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    result[i] = input[Math.floor(i * ratio)];
  }
  return floatTo16BitPCM(result);
}

export default function VoiceOrb() {
  const [state, setState] = useState<OrbState>("idle");
  const activeRef = useRef(false);

  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextPlaybackTimeRef = useRef(0);

  const stop = useCallback((nextState: OrbState = "idle") => {
    activeRef.current = false;
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    micCtxRef.current?.close().catch(() => {});
    wsRef.current?.close();

    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    micCtxRef.current = null;
    wsRef.current = null;

    setState(nextState);
  }, []);

  const playPcmChunk = useCallback((bytes: ArrayBuffer) => {
    if (!playbackCtxRef.current) {
      playbackCtxRef.current = new AudioContext();
      nextPlaybackTimeRef.current = playbackCtxRef.current.currentTime;
    }
    const ctx = playbackCtxRef.current;

    const int16 = new Int16Array(bytes);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;

    const buffer = ctx.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
    buffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    // Schedule chunks back-to-back so playback is gapless even though
    // they arrive over the network as separate messages.
    const startAt = Math.max(nextPlaybackTimeRef.current, ctx.currentTime);
    source.start(startAt);
    nextPlaybackTimeRef.current = startAt + buffer.duration;
  }, []);

  const start = useCallback(async () => {
    setState("listening");
    activeRef.current = true;

    const ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "state" && msg.state) {
            setState(msg.state as OrbState);
          } else if (msg.type === "error") {
            console.error("Voice pipeline error:", msg.message);
            stop("error");
          }
        } catch {
          // ignore malformed control messages
        }
        return;
      }
      playPcmChunk(event.data as ArrayBuffer);
    };

    ws.onerror = () => stop("error");
    ws.onclose = () => {
      if (activeRef.current) stop("error");
    };

    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.addEventListener(
        "error",
        () => reject(new Error("WebSocket failed to open")),
        { once: true },
      );
    });

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;

    const audioContext = new AudioContext();
    micCtxRef.current = audioContext;

    const source = audioContext.createMediaStreamSource(stream);
    sourceRef.current = source;

    // ScriptProcessorNode is deprecated but universally supported — swap
    // for an AudioWorklet later if you need to; fine for this scope.
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    processorRef.current = processor;

    processor.onaudioprocess = (event) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      const pcm16 = downsampleTo16k(input, audioContext.sampleRate);
      ws.send(pcm16.buffer as ArrayBuffer);
    };

    source.connect(processor);
    processor.connect(audioContext.destination);
  }, [playPcmChunk, stop]);

  // Stop everything if the component unmounts mid-session.
  useEffect(() => () => stop(), [stop]);

  const handleClick = () => {
    if (activeRef.current) {
      stop();
    } else {
      start().catch((err) => {
        console.error("Voice session failed to start:", err);
        stop("error");
      });
    }
  };

  return (
    <div className="voice-orb-wrap">
      <button
        type="button"
        className={`voice-orb voice-orb--${state}`}
        onClick={handleClick}
        aria-pressed={activeRef.current}
        aria-label={
          activeRef.current ? "Stop talking with Sahaara" : "Start talking with Sahaara"
        }
        style={{ ["--orb-color" as any]: STATE_COLOR[state] }}
      >
        <span className="voice-orb__core" />
      </button>
      <p className="voice-orb__label">{STATE_LABEL[state]}</p>
    </div>
  );
}