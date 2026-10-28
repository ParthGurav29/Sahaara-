import test from "node:test";
import assert from "node:assert/strict";

import {
  MOUTH_LEVEL_GAIN,
  mouthFromSignal,
  quantiseMouth,
  sameMouth,
  SILENT_MOUTH,
  smoothMouth,
} from "../src/mouthShape.ts";

const RATE = 48000;
const FFT = 1024;

/** Build byte-spectrum data with the given energy in a frequency band. */
function spectrum(peaks: Array<[number, number]>): Uint8Array {
  const bins = new Uint8Array(FFT / 2);
  const binHz = RATE / FFT;
  for (const [hz, value] of peaks) {
    const i = Math.round(hz / binHz);
    bins[i] = Math.max(bins[i], value);
  }
  return bins;
}

test("silence closes the mouth completely", () => {
  assert.deepEqual(mouthFromSignal(0, spectrum([]), RATE, FFT), SILENT_MOUTH);
  // Room tone below the noise floor must not twitch the face.
  assert.deepEqual(mouthFromSignal(0.005, spectrum([[300, 40]]), RATE, FFT), SILENT_MOUTH);
});

test("a loud vowel opens the mouth and silence closes it", () => {
  const bins = spectrum([[300, 200], [700, 160]]);
  const loud = mouthFromSignal(0.18, bins, RATE, FFT);
  assert.ok(loud.open > 0.8, `expected a wide opening, got ${loud.open}`);

  // Same spectrum, but the signal has dropped away: the jaw follows the sound.
  assert.equal(mouthFromSignal(0.002, bins, RATE, FFT).open, 0);
});

test("opening rises with loudness and never leaves 0–1", () => {
  const bins = spectrum([[300, 200]]);
  let previous = -1;
  for (const rms of [0.01, 0.05, 0.1, 0.15, 0.2, 0.4, 1]) {
    const { open } = mouthFromSignal(rms, bins, RATE, FFT);
    assert.ok(open >= previous, "open should be monotonic in loudness");
    assert.ok(open >= 0 && open <= 1, `open out of range: ${open}`);
    previous = open;
  }
  // Full-scale speech is allowed to reach the top.
  assert.equal(mouthFromSignal(1, bins, RATE, FFT).open, 1);
});

test("a bright sound is wider than a round one at the same loudness", () => {
  const round = mouthFromSignal(0.2, spectrum([[250, 210], [500, 120]]), RATE, FFT);
  const bright = mouthFromSignal(0.2, spectrum([[250, 210], [3000, 190]]), RATE, FFT);
  assert.ok(bright.wide > round.wide, `bright ${bright.wide} should exceed round ${round.wide}`);
  assert.ok(round.wide < 0.25, `round vowel should stay narrow, got ${round.wide}`);
  assert.ok(bright.wide > 0.4, `bright vowel should widen, got ${bright.wide}`);
});

test("width is gated by the opening — a shut mouth has no width", () => {
  const { wide } = mouthFromSignal(0.001, spectrum([[3000, 255]]), RATE, FFT);
  assert.equal(wide, 0);
});

test("missing or malformed spectrum data cannot break the opening", () => {
  const empty = mouthFromSignal(0.2, new Uint8Array(0), RATE, FFT);
  assert.ok(empty.open > 0.8, "the loudness path must still work without FFT bins");
  assert.equal(empty.wide, 0);

  for (const shape of [
    mouthFromSignal(0.2, spectrum([[300, 200]]), 0, FFT),
    mouthFromSignal(0.2, spectrum([[300, 200]]), RATE, 0),
    mouthFromSignal(Number.NaN, spectrum([[300, 200]]), RATE, FFT),
  ]) {
    assert.ok(Number.isFinite(shape.open) && Number.isFinite(shape.wide));
  }
});

test("attack is quicker than release, so syllables read separately", () => {
  const dt = 1 / 60;
  const opening = smoothMouth(SILENT_MOUTH, { open: 1, wide: 1 }, dt);
  assert.ok(opening.open > 0.5, `one frame should get most of the way open, got ${opening.open}`);

  const closing = smoothMouth({ open: 1, wide: 1 }, SILENT_MOUTH, dt);
  assert.ok(closing.open > 0.75, `one frame should not slam shut, got ${closing.open}`);
  assert.ok(closing.open < 1, "release must actually move");
});

test("a held shape converges and a bad dt is ignored", () => {
  let shape = SILENT_MOUTH;
  for (let i = 0; i < 120; i++) shape = smoothMouth(shape, { open: 1, wide: 0.5 }, 1 / 60);
  assert.ok(shape.open > 0.98 && shape.wide > 0.48, "smoothing should converge on the target");

  const held = { open: 0.4, wide: 0.2 };
  assert.deepEqual(smoothMouth(held, { open: 1, wide: 1 }, 0), held);
  assert.deepEqual(smoothMouth(held, { open: 1, wide: 1 }, Number.NaN), held);
});

test("quantising keeps the grid small and clamped", () => {
  assert.deepEqual(quantiseMouth({ open: 0.1234, wide: 0.777 }), { open: 0.12, wide: 0.78 });
  assert.deepEqual(quantiseMouth({ open: -3, wide: 9 }), { open: 0, wide: 1 });
  assert.deepEqual(quantiseMouth({ open: Number.NaN, wide: 0.5 }), { open: 0, wide: 0.5 });
  assert.ok(sameMouth({ open: 0.12, wide: 0.78 }, quantiseMouth({ open: 0.1234, wide: 0.777 })));
});

test("the loudness gain matches the calibration note", () => {
  // Speech RMS is ~0.15–0.2; at that level the mouth should be near open.
  assert.ok(0.15 * MOUTH_LEVEL_GAIN >= 0.7);
  assert.ok(0.2 * MOUTH_LEVEL_GAIN >= 0.9);
});
