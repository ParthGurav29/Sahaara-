/* useVoiceSession — the single owner of the live voice session.
 *
 * Extracted verbatim from VoiceOrb (which is gone) so the avatar can be the
 * clickable control without duplicating any of the delicate audio plumbing:
 *  - WebSocket to `/ws/voice` (session cookie authenticates the upgrade)
 *  - mic capture → 16 kHz PCM → WS
 *  - TTS PCM playback, scheduled gaplessly
 *  - the mic gate that stops Sahaara hearing her own TTS
 *  - AnalyserNode on the PLAYBACK path only, publishing 0–1 amplitude
 *
 * The zero-gain node on the mic branch and the gate in `onaudioprocess` are
 * both load-bearing. Do not "simplify" either one away: muting a GainNode on
 * the silent speaker branch (the old code) never stopped bytes reaching the
 * ASR socket, which made Sahaara answer herself.
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { wsUrl } from "./api";
import { useVoiceActions, type VoiceStateName } from "./VoiceStateContext";

export type VoiceSessionState = VoiceStateName;

const WS_URL = wsUrl("/ws/voice");

// Must match voice/asr.py's Deepgram config (linear16, 16000Hz, mono).
const MIC_SAMPLE_RATE = 16000;
// Must match voice/tts.py's Cartesia output_format (pcm_s16le, 22050Hz, mono).
const PLAYBACK_SAMPLE_RATE = 22050;
// Keep the mic closed briefly after Sahaara stops speaking. The speaker tail
// would otherwise be captured by the mic and re-transcribed as a new turn.
const MIC_UNMUTE_DELAY_MS = 500;

// Amplitude smoothing (A4 owner parameters).
const ATTACK_SECONDS = 0.05; // 50 ms
const RELEASE_SECONDS = 0.15; // 150 ms

// Mouth amplitude comes from the RMS of the TTS waveform itself. Two earlier
// frequency-domain attempts were wrong: the bin band assumed a 22050 Hz context
// (a 48 kHz context summed 937–7312 Hz) and `sqrt(mean)/255` caps at 0.0625, so
// the mouth could never open past ~6% of its range. Time-domain RMS is the
// honest loudness envelope, and the analyser still sits on the PLAYBACK path.
const ANALYSER_FFT_SIZE = 1024; // ~21 ms window at 48 kHz
const LEVEL_GAIN = 5; // speech RMS ~0.15–0.2 → fully open

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

export interface VoiceSession {
  state: VoiceSessionState;
  isActive: boolean;
  toggle: () => void;
  initiativePrompt: string | null;
  dismissInitiative: () => void;
  /** Why `state === "error"`: a refused mic is not a dropped connection. */
  errorKind: "connection" | "microphone" | null;
  /** Rendered by the caller so radio streams have somewhere to play. */
  audioRef: RefObject<HTMLAudioElement | null>;
}

export function useVoiceSession(): VoiceSession {
  const { setState: setGlobalState, setIsActive: setGlobalActive, setLevel } = useVoiceActions();

  const [state, setState] = useState<VoiceSessionState>("idle");
  const [isActive, setIsActiveLocal] = useState(false);
  const [initiativePrompt, setInitiativePrompt] = useState<string | null>(null);
  // Why the session ended in "error". A refused microphone is not a lost
  // connection, and telling someone the connection dropped when their mic
  // permission was denied sends them after the wrong problem.
  const [errorKind, setErrorKind] = useState<"connection" | "microphone" | null>(null);

  const activeRef = useRef(false);
  const mountedRef = useRef(true);
  const stateRef = useRef<VoiceSessionState>("idle");

  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const playbackCtxRef = useRef<AudioContext | null>(null);
  const nextPlaybackTimeRef = useRef(0);
  const micMutedRef = useRef(false);
  const pendingPlaybackRef = useRef(0);
  const unmuteTimerRef = useRef<number | null>(null);

  // Audio amplitude analysis (AnalyserNode on the playback path only).
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastPublishedRef = useRef(0);
  const timeDataRef = useRef<Float32Array<ArrayBuffer> | null>(null);
  const lastLevelRef = useRef(0);

  // Radio stream handling.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const radioStreamUrlRef = useRef<string | null>(null);
  const isRadioPlayingRef = useRef(false);

  // Sync local state to the global context (tier comes from EscalationLadder,
  // which is the only other writer).
  useEffect(() => {
    setGlobalState(state);
    setGlobalActive(isActive);
  }, [state, isActive, setGlobalState, setGlobalActive]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Single source of truth for "should the mic be sent to Deepgram?".
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

  const stop = useCallback(
    (nextState: VoiceSessionState = "idle", kind: "connection" | "microphone" | null = null) => {
      activeRef.current = false;
      processorRef.current?.disconnect();
      sourceRef.current?.disconnect();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      micCtxRef.current?.close().catch(() => {});
      wsRef.current?.close();

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
      lastLevelRef.current = 0;
      lastPublishedRef.current = 0;
      stateRef.current = nextState;

      processorRef.current = null;
      sourceRef.current = null;
      streamRef.current = null;
      micCtxRef.current = null;
      wsRef.current = null;

      if (mountedRef.current) {
        setState(nextState);
        setIsActiveLocal(false);
        setErrorKind(nextState === "error" ? (kind ?? "connection") : null);
      }
    },
    [setLevel],
  );

  // ---- Radio stream control ----

  const startRadio = useCallback(
    (url: string) => {
      const audio = audioRef.current;
      if (!audio) return;

      if (audio.src && audio.src !== url) {
        audio.pause();
        audio.src = "";
      }

      if (audio.src === url && !audio.paused) {
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

      // Mute mic while radio plays so ASR doesn't pick up broadcast audio.
      micMutedRef.current = true;
      syncMicGate();
    },
    [syncMicGate],
  );

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
  }, [syncMicGate]);

  const playPcmChunk = useCallback(
    (bytes: ArrayBuffer) => {
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

      // Create or reuse AnalyserNode on the playback path (never on mic path).
      if (!analyserRef.current) {
        analyserRef.current = ctx.createAnalyser();
        analyserRef.current.fftSize = ANALYSER_FFT_SIZE;
        // No smoothingTimeConstant: a time-domain read is unsmoothed, and the
        // 50/150 ms attack/release below is the only smoothing we want.
        analyserRef.current.connect(ctx.destination);
        timeDataRef.current = new Float32Array(analyserRef.current.fftSize);
      }
      // Playback chain: source -> analyser -> destination
      source.connect(analyserRef.current);

      // Keep the mic closed while any Sahaara audio is scheduled.
      pendingPlaybackRef.current += 1;
      syncMicGate();

      // Start the RAF amplitude loop if it is not already running.
      if (rafRef.current === null && analyserRef.current && timeDataRef.current) {
        const tick = () => {
          const analyser = analyserRef.current;
          const timeData = timeDataRef.current;
          if (!analyser || !timeData) {
            rafRef.current = null;
            return;
          }
          analyser.getFloatTimeDomainData(timeData);

          // RMS of the TTS waveform = its loudness envelope.
          let sumSq = 0;
          for (let i = 0; i < timeData.length; i++) sumSq += timeData[i] * timeData[i];
          const rms = Math.sqrt(sumSq / timeData.length);
          const raw = Math.min(1, rms * LEVEL_GAIN);

          const dt = 1 / 60;
          const alpha = raw > lastLevelRef.current ? ATTACK_SECONDS : RELEASE_SECONDS;
          const smoothed =
            lastLevelRef.current + (raw - lastLevelRef.current) * (1 - Math.exp(-dt / alpha));
          lastLevelRef.current = smoothed;

          // Quantise to 1/50 so a steady tone does not re-render React 60×/s.
          const quantised = Math.round(Math.max(0, Math.min(1, smoothed)) * 50) / 50;
          if (quantised !== lastPublishedRef.current) {
            lastPublishedRef.current = quantised;
            setLevel(quantised);
          }

          // Continue while audio is playing or level is above noise floor.
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

      // Schedule chunks back-to-back so playback is gapless.
      const startAt = Math.max(nextPlaybackTimeRef.current, ctx.currentTime);
      source.start(startAt);
      nextPlaybackTimeRef.current = startAt + buffer.duration;

      source.onended = () => {
        pendingPlaybackRef.current = Math.max(0, pendingPlaybackRef.current - 1);
        syncMicGate();
      };
    },
    [syncMicGate, setLevel],
  );

  const start = useCallback(async () => {
    if (activeRef.current) return;

    activeRef.current = true;
    stateRef.current = "listening";
    micMutedRef.current = false;
    pendingPlaybackRef.current = 0;
    lastLevelRef.current = 0;
    lastPublishedRef.current = 0;
    if (mountedRef.current) {
      setState("listening");
      setIsActiveLocal(true);
      setErrorKind(null);
    }

    // The session cookie authenticates the upgrade; no token in the URL.
    const ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onmessage = (event) => {
      // A stopped session must never be flipped back to "speaking" by a frame
      // that was already in flight when `stop()` closed the socket.
      if (!activeRef.current) return;
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "state" && msg.state) {
            const nextState = msg.state as VoiceSessionState;
            stateRef.current = nextState;
            setState(nextState);
            syncMicGate();
          } else if (msg.type === "error") {
            console.error("Voice pipeline error:", msg.message);
            stop("error");
          } else if (msg.type === "initiative") {
            // Proactive initiative from the scheduler (text only; the mic gate
            // stays under the turn-state machine's control).
            if (msg.text) {
              setInitiativePrompt(msg.text);
            }
          } else if (msg.type === "play_stream") {
            const url = msg.url;
            if (url) startRadio(url);
          } else if (msg.type === "stop_media") {
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
      ws.addEventListener("error", () => reject(new Error("WebSocket failed to open")), {
        once: true,
      });
    });

    // The user may have tapped stop while the socket was opening.
    if (!activeRef.current) {
      ws.close();
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Browser-level echo cancellation is the first line of defence
          // against Sahaara's speaker output re-entering the mic; the
          // turn-state gate below is the deterministic backstop.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      // Denied permission, no device, or a busy device. Say so honestly
      // instead of blaming the connection.
      console.error("Microphone unavailable:", err);
      stop("error", "microphone");
      return;
    }

    if (!activeRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      ws.close();
      return;
    }
    streamRef.current = stream;

    const audioContext = new AudioContext();
    micCtxRef.current = audioContext;

    const source = audioContext.createMediaStreamSource(stream);
    sourceRef.current = source;

    // ScriptProcessorNode is deprecated but universally supported — swap for
    // an AudioWorklet later if needed; fine for this scope.
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

    // Zero-gain node prevents mic audio reaching the speakers and keeps the
    // ScriptProcessorNode pulling audio. The real mute happens above.
    const zeroGain = audioContext.createGain();
    zeroGain.gain.value = 0;
    source.connect(processor);
    processor.connect(zeroGain);
    zeroGain.connect(audioContext.destination);
  }, [syncMicGate, playPcmChunk, stop, startRadio, stopRadio]);

  const toggle = useCallback(() => {
    if (activeRef.current) {
      stop();
      return;
    }
    start().catch((err) => {
      console.error("Voice session failed to start:", err);
      stop("error");
    });
  }, [start, stop]);

  const dismissInitiative = useCallback(() => setInitiativePrompt(null), []);

  // Stop everything if the component unmounts mid-session.
  useEffect(() => () => stop(), [stop]);

  return {
    state,
    isActive,
    toggle,
    initiativePrompt,
    dismissInitiative,
    errorKind,
    audioRef,
  };
}
