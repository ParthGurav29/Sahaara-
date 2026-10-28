import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";
import { useVoiceActions } from "./VoiceStateContext";
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

const WS_URL = wsUrl("/ws/voice");

// Must match voice/asr.py's Deepgram config (linear16, 16000Hz, mono).
const MIC_SAMPLE_RATE = 16000;
// Must match voice/tts.py's Cartesia output_format (pcm_s16le, 22050Hz, mono).
const PLAYBACK_SAMPLE_RATE = 22050;
// Keep the mic closed briefly after Sahaara stops speaking. The speaker tail
// would otherwise be captured by the mic and re-transcribed as a new turn.
const MIC_UNMUTE_DELAY_MS = 500;

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
  const { setState: setGlobalState, setIsActive, setLevel } = useVoiceActions();
  const [state, setState] = useState<OrbState>("idle");
  const activeRef = useRef(false);
  const [initiativePrompt, setInitiativePrompt] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextPlaybackTimeRef = useRef(0);
  const stateRef = useRef<OrbState>("idle");
  const micMutedRef = useRef(false);
  const pendingPlaybackRef = useRef(0);
  const unmuteTimerRef = useRef<number | null>(null);

  // Audio amplitude analysis (AnalyserNode on playback path only)
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastPublishedRef = useRef(0);
  const freqDataRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const lastLevelRef = useRef(0);

  // Radio stream handling
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const radioStreamUrlRef = useRef<string | null>(null);
  const isRadioPlayingRef = useRef(false);

  // Sync local state to global context whenever it changes
  useEffect(() => {
    setGlobalState(state);
    setIsActive(activeRef.current);
  }, [state, setGlobalState, setIsActive]);

  // Single source of truth for "should the mic be sent to Deepgram?".
  // The old code adjusted a GainNode that was only wired to the silent
  // speaker path, so it never actually muted the WebSocket send — Sahaara's
  // own TTS was picked up by the mic and triggered a second turn.
  const syncMicGate = useCallback(() => {
    const busy =
      stateRef.current === "thinking" ||
      stateRef.current === "speaking" ||
      pendingPlaybackRef.current > 0;

    if (busy) {
      if (unmuteTimerRef.current !== null) {
        window.clearTimeout(unmuteTimerRef.current);
        unmuteTimerRef.current = null;
      }
      micMutedRef.current = true;
      return;
    }

    if (!micMutedRef.current) return;
    if (unmuteTimerRef.current !== null) {
      window.clearTimeout(unmuteTimerRef.current);
    }
    unmuteTimerRef.current = window.setTimeout(() => {
      unmuteTimerRef.current = null;
      if (stateRef.current === "listening" && pendingPlaybackRef.current === 0) {
        micMutedRef.current = false;
      }
    }, MIC_UNMUTE_DELAY_MS);
  }, []);

  const stop = useCallback((nextState: OrbState = "idle") => {
    activeRef.current = false;
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    micCtxRef.current?.close().catch(() => {});
    wsRef.current?.close();

    // Stop amplitude analysis RAF loop
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      setLevel(0);
    }

    if (unmuteTimerRef.current !== null) {
      window.clearTimeout(unmuteTimerRef.current);
      unmuteTimerRef.current = null;
    }
    pendingPlaybackRef.current = 0;
    micMutedRef.current = false;
    stateRef.current = nextState;

    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current = null;
    micCtxRef.current = null;
    wsRef.current = null;

    setState(nextState);
  }, [setLevel]);

  // ---- Radio stream control ----

  const startRadio = useCallback((url: string) => {
    const audio = audioRef.current;
    if (!audio) return;

    // Stop any existing playback first
    if (audio.src && audio.src !== url) {
      audio.pause();
      audio.src = "";
    }

    if (audio.src === url && !audio.paused) {
      // Already playing this stream
      return;
    }

    audio.src = url;
    audio.play().catch((err) => {
      console.error("Radio playback failed:", err);
      radioStreamUrlRef.current = null;
      isRadioPlayingRef.current = false;
    });
    radioStreamUrlRef.current = url;
    isRadioPlayingRef.current = true;

    // Mute mic while radio plays so ASR doesn't pick up broadcast audio
    micMutedRef.current = true;
    syncMicGate();
  }, []);

  const stopRadio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.src = "";
    }
    radioStreamUrlRef.current = null;
    isRadioPlayingRef.current = false;
    micMutedRef.current = false;
    syncMicGate();
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

    // Create or reuse AnalyserNode on the playback path (never on mic path)
    if (!analyserRef.current) {
      analyserRef.current = ctx.createAnalyser();
      analyserRef.current.fftSize = 256;              // 128 frequency bins
      analyserRef.current.smoothingTimeConstant = 0.3;
      analyserRef.current.connect(ctx.destination);
      freqDataRef.current = new Uint8Array(analyserRef.current.frequencyBinCount);
    }
    // Playback chain: source -> analyser -> destination
    source.connect(analyserRef.current);

    // Keep the mic closed while any Sahaara audio is scheduled.
    pendingPlaybackRef.current += 1;
    syncMicGate();

    // Start RAF amplitude loop if not already running
    if (rafRef.current === null && analyserRef.current && freqDataRef.current) {
      const attack = 0.05;   // 50ms attack
      const release = 0.15;  // 150ms release

      const tick = () => {
        const analyser = analyserRef.current;
        const freqData = freqDataRef.current;
        if (!analyser || !freqData) {
          rafRef.current = null;
          return;
        }
        analyser.getByteFrequencyData(freqData);

        // Speech band: bins ~5–40 (≈100–4000 Hz at 22050 Hz / 256 FFT)
        let sum = 0;
        for (let i = 5; i < 40; i++) sum += freqData[i];
        const rms = Math.sqrt(sum / 35) / 255;  // 0–1

        // Attack/release smoothing
        const dt = 1 / 60;
        const alpha = rms > lastLevelRef.current ? attack : release;
        const smoothed = lastLevelRef.current + (rms - lastLevelRef.current) * (1 - Math.exp(-dt / alpha));
        lastLevelRef.current = smoothed;

        // Quantise to 1/50 so a steady tone does not re-render React 60×/s.
        const quantised = Math.round(Math.max(0, Math.min(1, smoothed)) * 50) / 50;
        if (quantised !== lastPublishedRef.current) {
          lastPublishedRef.current = quantised;
          setLevel(quantised);
        }

        // Continue while audio is playing or level is above noise floor
        if (pendingPlaybackRef.current > 0 || smoothed > 0.01) {
          rafRef.current = requestAnimationFrame(tick);
        } else {
          rafRef.current = null;
          lastPublishedRef.current = 0;
          setLevel(0);
        }
      };
      rafRef.current = requestAnimationFrame(tick);
    }

    // Schedule chunks back-to-back so playback is gapless
    const startAt = Math.max(nextPlaybackTimeRef.current, ctx.currentTime);
    source.start(startAt);
    nextPlaybackTimeRef.current = startAt + buffer.duration;

    source.onended = () => {
      pendingPlaybackRef.current = Math.max(0, pendingPlaybackRef.current - 1);
      syncMicGate();
    };
  }, [syncMicGate, setLevel]);

  const start = useCallback(async () => {
    setState("listening");
    stateRef.current = "listening";
    micMutedRef.current = false;
    pendingPlaybackRef.current = 0;
    activeRef.current = true;

    // The session cookie authenticates the upgrade; no token in the URL.
    const ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "state" && msg.state) {
            const nextState = msg.state as OrbState;
            stateRef.current = nextState;
            setState(nextState);
            syncMicGate();
          } else if (msg.type === "error") {
            console.error("Voice pipeline error:", msg.message);
            stop("error");
          } else if (msg.type === "initiative") {
            // Proactive initiative from scheduler (text-only; the orb gate
            // stays under the turn-state machine's control).
            if (msg.text) {
              setInitiativePrompt(msg.text);
            }
          } else if (msg.type === "play_stream") {
            // Start playing the radio stream
            const url = msg.url;
            if (url) {
              startRadio(url);
            }
          } else if (msg.type === "stop_media") {
            // Stop any playing audio
            stopRadio();
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

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        // Browser-level echo cancellation is the first line of defence
        // against Sahaara's speaker output re-entering the mic; the
        // turn-state gate below is the deterministic backstop.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
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
      // This is the actual ASR send path. Muting a GainNode on the silent
      // speaker branch (the old code) never stopped these bytes from being
      // sent, which is why Sahaara's own TTS triggered a second turn.
      if (micMutedRef.current) return;
      const input = event.inputBuffer.getChannelData(0);
      const pcm16 = downsampleTo16k(input, audioContext.sampleRate);
      ws.send(pcm16.buffer as ArrayBuffer);
    };

    // Zero-gain node prevents mic audio from reaching speakers and keeps
    // the ScriptProcessorNode pulling audio. The real mute happens in
    // onaudioprocess above.
    const zeroGain = audioContext.createGain();
    zeroGain.gain.value = 0;
    source.connect(processor);
    processor.connect(zeroGain);
    zeroGain.connect(audioContext.destination);
  }, [syncMicGate, playPcmChunk, stop, startRadio, stopRadio]);

  // Stop everything if the component unmounts mid-session.
  useEffect(() => () => stop(), [stop, startRadio, stopRadio]);

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
      {/* Hidden audio element for radio streaming */}
      <audio ref={audioRef} />
      <p className="voice-orb__label">{STATE_LABEL[state]}</p>
      {initiativePrompt && (
        <div className="voice-initiative-prompt" role="status" aria-live="polite">
          {initiativePrompt}
          <button
            className="voice-initiative-dismiss"
            onClick={() => setInitiativePrompt(null)}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}