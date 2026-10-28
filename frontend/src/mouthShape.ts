/* mouthShape — turns the TTS signal into a mouth shape.
 *
 * Pure maths, no Web Audio, no React: the hook reads the analyser and this file
 * decides what the face should do, so it can be unit-tested and calibrated
 * against a real recording.
 *
 * Two values, not twenty visemes:
 *   open — how far the jaw has dropped (0 = closed, 1 = wide open)
 *   wide — how wide/rounded the mouth is, from the spectral brightness
 *          (an "ee" has far more 2–5 kHz energy than an "oo")
 *
 * The opening is deliberately a single shape that scales. Phoneme-perfect
 * visemes would need timings from the TTS service; this is the cartoon-grade
 * version that stays in sync because it reads the audio actually playing.
 */

export interface MouthShape {
  /** 0 = closed, 1 = fully open. */
  open: number;
  /** 0 = rounded/closed corners, 1 = wide. */
  wide: number;
}

export const SILENT_MOUTH: MouthShape = { open: 0, wide: 0 };

/** Speech needs a fast attack to catch a syllable onset… */
export const MOUTH_ATTACK_SECONDS = 0.018;
/** …and a short release so M/B/P closures actually close between words. */
export const MOUTH_RELEASE_SECONDS = 0.07;

/** Speech RMS is roughly 0.15–0.2; ×5 makes ordinary speech reach the top. */
export const MOUTH_LEVEL_GAIN = 5;

/** Below this the room tone must not twitch the mouth. */
const NOISE_FLOOR = 0.04;

// Vowel body, then the band that separates a wide "ee" from a round "oo".
const LOW_BAND_HZ: readonly [number, number] = [120, 900];
const HIGH_BAND_HZ: readonly [number, number] = [1800, 5000];

// Brightness is high / (low + high). Measured on two real Cartesia replies
// (183 voiced frames each): p05 0.26–0.28, median 0.38–0.40, p95 0.53–0.55.
// The band is deliberately narrow inside that range — mapping 0.2–0.6 made
// every vowel look the same, so the width never visibly moved.
const BRIGHTNESS_FLOOR = 0.27;
const BRIGHTNESS_CEILING = 0.52;

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

/**
 * Level of a frequency band: the mean of its TOP_BINS strongest bins.
 *
 * A plain RMS over the band was wrong — the 1800–5000 Hz band is four times
 * wider than 120–900 Hz, so the same energy scored four times lower and every
 * vowel came out "round". Peak-bin energy is comparable across bandwidths and
 * is what actually distinguishes an "ee" from an "oo".
 */
const TOP_BINS = 4;

function bandLevel(
  bins: Uint8Array,
  sampleRate: number,
  fftSize: number,
  band: readonly [number, number],
): number {
  if (!bins.length || sampleRate <= 0 || fftSize <= 0) return 0;
  const binHz = sampleRate / fftSize;
  const first = Math.max(1, Math.floor(band[0] / binHz));
  const last = Math.min(bins.length - 1, Math.ceil(band[1] / binHz));
  if (last < first) return 0;

  const top: number[] = [];
  for (let i = first; i <= last; i++) {
    const v = bins[i] / 255;
    if (top.length < TOP_BINS) {
      top.push(v);
      top.sort((a, b) => a - b);
    } else if (v > top[0]) {
      top[0] = v;
      top.sort((a, b) => a - b);
    }
  }
  return top.reduce((sum, v) => sum + v, 0) / top.length;
}

/**
 * Map one analyser frame to a mouth shape.
 *
 * `rms` is the time-domain loudness of the same frame (the honest envelope),
 * `bins` is the byte frequency data — used only for the width, so a bug in the
 * spectrum can never stop the mouth opening.
 */
export function mouthFromSignal(
  rms: number,
  bins: Uint8Array,
  sampleRate: number,
  fftSize: number,
): MouthShape {
  const level = clamp01(rms * MOUTH_LEVEL_GAIN);
  if (level < NOISE_FLOOR) return SILENT_MOUTH;

  // Slightly eased so quiet syllables still read as movement.
  const open = Math.pow(level, 0.85);

  const low = bandLevel(bins, sampleRate, fftSize, LOW_BAND_HZ);
  const high = bandLevel(bins, sampleRate, fftSize, HIGH_BAND_HZ);
  const brightness = high / (low + high + 1e-4);
  const width = clamp01(
    (brightness - BRIGHTNESS_FLOOR) / (BRIGHTNESS_CEILING - BRIGHTNESS_FLOOR),
  );

  // Nothing moves while the mouth is shut.
  return { open, wide: width * open };
}

/** One smoothing step. Opening is faster than closing, like a real jaw. */
export function smoothMouth(previous: MouthShape, target: MouthShape, dt: number): MouthShape {
  if (!Number.isFinite(dt) || dt <= 0) return previous;
  const step = (from: number, to: number): number => {
    const tau = to > from ? MOUTH_ATTACK_SECONDS : MOUTH_RELEASE_SECONDS;
    const k = 1 - Math.exp(-dt / tau);
    return from + (to - from) * k;
  };
  return {
    open: clamp01(step(previous.open, target.open)),
    wide: clamp01(step(previous.wide, target.wide)),
  };
}

/**
 * Snap to a grid so React only re-renders when the face visibly changes.
 * 1/100 on the opening, 1/50 on the width.
 */
export function quantiseMouth(shape: MouthShape): MouthShape {
  return {
    open: Math.round(clamp01(shape.open) * 100) / 100,
    wide: Math.round(clamp01(shape.wide) * 50) / 50,
  };
}

export function sameMouth(a: MouthShape, b: MouthShape): boolean {
  return a.open === b.open && a.wide === b.wide;
}
