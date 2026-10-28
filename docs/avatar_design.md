# Sahaara Avatar — Design Decision (A1)

**Date:** 2026-10-28  
**Decision:** Option 1 — Abstract Breathing Orb + Soft Face  
**Status:** Implemented. The avatar fully replaced `VoiceOrb` on 2026-10-28 — it is
the only voice visual **and** the only voice control. Since A7 (2026-10-28) it also
carries the fixed task set (timer, reminder, time, radio, music, stop).

---

## Selected Style: Abstract Breathing Orb + Soft Face

A circular orb in the same low-arousal palette as the old `VoiceOrb`, with subtle
"face" elements added:

- **Eyes:** Two soft glow points (small radial gradients) at ~35%/65% horizontal, 35% vertical
- **Mouth:** A horizontal line at ~65% vertical that opens/closes with the real TTS amplitude

No anatomical proportions, no human features beyond abstract suggestion.

The **whole avatar is the button**. Clicking it starts the voice session; clicking
while active stops it — identical behaviour to the old orb, with hover lift,
press, focus ring and a soft presence ring while live.

The label under it states the current state. Two distinct error lines are used,
because a refused microphone is the most likely failure on a phone and calling
it a lost connection sends someone after the wrong problem:

- socket failure → *"Connection lost — tap to retry"*
- `getUserMedia` denied/unavailable → *"Microphone unavailable — tap to retry"*

---

## State Visual Behaviors

| State | Color (from VoiceOrb) | Eyes | Mouth | Motion |
|-------|----------------------|------|-------|--------|
| **Idle** | `#C9C2B8` (stone) | Soft, resting (opacity 0.45) | Short closed line | Very slow glow pulse |
| **Listening** | `#7C93A8` (slate blue) | Brighter (opacity 0.85), steady | Short closed line | Slow 2–3° tilt, 8s base period |
| **Thinking** | `#A98FC7` (lavender) | Gentle blink (opacity 0.85→0.25→0.85, 4s base) | Short closed line | Slow pulse (3.6s base) |
| **Speaking** | `#E8A85C` (warm amber) | Bright (opacity 0.85), steady | **Amplitude-driven** (0–1 from the TTS waveform) | Mouth opens/closes, subtle glow pulse |
| **Error** | `#C2716B` (muted clay) | Bright (opacity 0.85) | Short closed line | Static — all animation off, never a flash |

Eye opacity values were raised twice: the A2 draft (0.15 idle / 0.6 active) made
the face invisible at 130 px, and A6 raised them again to 0.45 / 0.85 after
looking at the rendered avatar on the Live tab. The eye gradient was also
changed from the state colour (near-white where the eyes sit) to a neutral core
so the points stay visible on every state colour, and the mouth stroke moved
from `var(--orb-color)` to the same neutral dark (`#5f5a54`): amber-on-amber was
an unreadable smudge.

---

## Tier Calmness Mapping (Orange/Red = Calmer, Never Urgent)

Live tier comes from the same SSE/WS `tier_change` stream the caregiver ladder
uses: `EscalationLadder` pushes it into `VoiceStateContext` and `SahaaraAvatar`
reads it from there (it also drives the live-session ring). It is seeded from
`GET /escalation/status` on load and on every socket open, so a reload
mid-escalation does not show green.

CSS custom properties driven by `tier` prop:

| Tier | `--anim-speed` | `--glow-opacity` | `--mouth-scale` |
|------|----------------|------------------|-----------------|
| **Green** | 1.0 | 0.40 | 1.0 |
| **Yellow** | 0.8 | 0.30 | 0.9 |
| **Orange** | 0.6 | 0.20 | 0.7 |
| **Red** | 0.4 | 0.15 | 0.5 |

- All animations (tilt, pulse, blink, mouth) multiply their base duration by `1 / --anim-speed`
- Glow opacity and mouth amplitude scale down — **softer, slower, quieter**
- No red flashing, no shaking, no wide-eyed expressions, no faster motion
- **Verified in the browser (A5):** a danger turn (`"I fell and I cant get up"`)
  takes the avatar from `--anim-speed: 1` / `--mouth-scale: 1` to
  `0.4` / `0.5` / glow `0.15`, matching the ladder flipping to RED.

### Mouth geometry

> **Superseded by A8.** The 130×130 dash-array mouth below belonged to the old
> abstract orb. A8 draws a real smile on a 400×400 character and gives it two
> nested groups: a gentle 0.85 s speaking loop and the real TTS amplitude.

The mouth is a single 46-unit line in the 130×130 viewBox. It rests at 10 units
(a calm, closed line) and opens symmetrically to `10 + 36 × level × mouth-scale`
using `stroke-dasharray` + a centring `stroke-dashoffset` — so it grows from the
middle rather than sliding in from one side.

### Amplitude source (A6 — two bugs fixed)

The mouth is driven by the **RMS of the TTS waveform**
(`AnalyserNode.getFloatTimeDomainData`, `fftSize` 1024, ≈21 ms window at 48 kHz),
then smoothed with the owner's 50 ms attack / 150 ms release and quantised to
1/50 before it reaches React. The analyser is attached to the **playback chain
only** (`source → analyser → destination`); the mic path keeps its zero-gain
node and its send-path gate untouched.

The original A4 implementation read the **frequency** domain and was broken in
two independent ways that had never been observed live:

1. It summed bins 5–40 as "≈100–4000 Hz at 22050 Hz", but the `AnalyserNode`
   inherits the **AudioContext** rate (48 kHz in practice), so the band was
   really 937–7312 Hz — the top of speech, not its body.
2. `Math.sqrt(mean) / 255` caps at **0.0625** even for a full-scale signal. The
   mouth could never open past ~6% of its range; measured on a synthetic burst
   it moved 10 → 10.72 units out of a possible 46.

Measured after the fix, injecting real PCM through the production playback path
in headless Chrome: the mouth ran **10 → 46** with syllable-level variation
(30.9 … 46) and released smoothly back to 10. No lip-sync tooling is used on
live replies — the mouth is amplitude only, as required.

---

## Non-Human Guarantees (Hard Constraints)

1. **Geometry only:** Circles, lines, radial gradients. No paths resembling eyes/nose/mouth in human arrangement.
2. **Color palette fixed:** Only the 4 VoiceOrb colors + transparency. No skin tones, no warm pinks/reds that suggest flesh.
3. **Proportions abstract:** Eye points at 35%/65% horizontal (not human 30%/70%), mouth at 65% vertical (not human 70%+).
4. **No animation suggests personality:** No winking, smiling, frowning, surprise. Only state-driven: attentive, processing, speaking.

---

## Technical Approach

- **Inline SVG** in `SahaaraAvatar.tsx` — no external assets, no build step
- **CSS custom properties** for all dynamic values (`--orb-color`, `--anim-speed`, `--glow-opacity`, `--mouth-scale`)
- **Single `requestAnimationFrame` loop** for audio-reactive mouth (A4/A6) — reads the playback RMS, writes the level into `VoiceStateContext`
- **`prefers-reduced-motion`** respected via existing CSS pattern (disable animations, colour-only state)
- **Zero dependencies** — matches project constraint

---

## Files

```
frontend/src/
├── useVoiceSession.ts     # The whole voice session: WS, mic, TTS playback,
│                          #   mic gate, analyser, radio, initiative prompt
├── OrbCharacter.tsx       # The mascot drawing only (A8): sphere, face, leaves, bowl
├── OrbCharacter.css       # Character motion + per-state expressions
├── SahaaraAvatar.tsx      # AvatarVisual (SVG shell + CSS vars) + SahaaraAvatar
├── SahaaraAvatar.css      # Control, label, session ring, fallback
├── VoiceStateContext.tsx  # state + tier / level / stable-action contexts
└── AvatarPreview.tsx      # dev-only `?avatar-preview=true` harness
```

`VoiceOrb.tsx` and `VoiceOrb.css` were **deleted** in A6. The session logic they
held lives in `useVoiceSession.ts` unchanged apart from the two amplitude fixes
and a guard that ignores socket frames arriving after `stop()`.

**Props:**

```ts
// pure visual, used by the dev preview
interface AvatarVisualProps {
  state: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
  level?: number;                                  // 0–1 amplitude
  tier?: 'green' | 'yellow' | 'orange' | 'red';    // defaults to 'green'
  size?: number;                                   // rendered px, default 130
}

// the control — reads state/level/tier from context, owns the session
function SahaaraAvatar(): JSX.Element
```

**Dev preview:** `?avatar-preview=true` (DEV only, rendered above the auth gates)
with state/tier/level controls and an auto-speak simulation.

---

## Phase Status

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **A1** | This decision memo | ✅ Done |
| **A2** | `SahaaraAvatar.tsx` / `.css` + dev preview | ✅ Done |
| **A3** | `VoiceStateContext` + Orb/Avatar toggle + ErrorBoundary fallback | ✅ Done (toggle retired in A6) |
| **A4** | Audio-reactive mouth (AnalyserNode on the playback path, 50 ms attack / 150 ms release) | ✅ Done, amplitude math fixed in A6 |
| **A5** | Tier calmness wired from the live escalation stream | ✅ Done (browser-verified) |
| **A6** | Orb replaced entirely: shared `useVoiceSession` hook, clickable avatar, amplitude fix, legibility pass | ✅ Done (browser-verified) |
| **A7** | Tests + docs | 🟡 Docs done; no unit tests for the SVG (verified in-browser instead) |
| **A8** | Mascot redesign: porcelain sphere + jade cradle, matched to the owner's reference image | ✅ Done (browser-verified, screenshot reviewed) |

### Wiring note (found while wiring A5)

`AppContent` rendered `<VoiceStateProvider>` inside its own JSX, so a
`useVoiceStateReadOnly()` call in `AppContent` was **outside** the provider and
always returned the defaults — the avatar was permanently idle/green. The
orb/avatar section was moved into a child component (`AshaVisual`) that sits
inside the provider. The context was also split into three parts (state + tier,
level, stable actions) so the 60 fps amplitude updates re-render only the
avatar, not the whole caregiver dashboard.

### A6 verification (headless Chrome, real playback path)

`page.evaluate` in this driver runs in an **isolated world**, so page globals
cannot be stubbed from it; the harness had to inject with `addInitScript`. With
a fake `/ws/voice` socket feeding real PCM and a fake mic stream:

| Check | Result |
|---|---|
| Orb gone | `document.querySelectorAll('.voice-orb, .voice-orb-wrap, .avatar-toggle').length === 0` |
| Click starts | idle → listening → thinking → speaking, `aria-pressed=true`, presence ring animates |
| Mouth tracks TTS | `--mouth-dash` 10 → 46, 30.9–46 through the burst, releases 46 → 10 |
| Click stops | back to idle, `aria-pressed=false`, mouth 10, ring removed |
| Red tier | `--anim-speed 0.4`, `--glow-opacity 0.15`, `--mouth-scale 0.5` (calmer, not alarming) |
| Console | no app errors (only the harness's own blocked local-network WS) |

## A7 — the avatar is also the task interface (2026-10-28)

Because the avatar is now the only voice control, it also has to *do* things
when asked. It handles a **fixed list** — time, timer, reminder, list, cancel,
radio, local music, stop — and nothing open-ended.

Rules that follow from the avatar being the only interface:

- **Deterministic replies.** A task answer is built from the action result, not
  from the model, so the words on screen can never disagree with what happened.
- **Calm on failure.** A dead radio stream, an unknown song or an unknown
  station produces a quiet sentence (*"The radio isn't answering right now."*),
  not an error state. Nothing on the avatar changes.
- **Timers respect the room.** With no session open a timer fires on the
  caregiver dashboard and stays silent; a new session never opens with a stale
  announcement. A timer that fires while the avatar is live is *spoken* through
  the same Cartesia voice, with the orb in `speaking` so the mic gate closes.
- **Escalation still wins.** Task handling runs after the escalation engine and
  is skipped on danger / physical turns — an emergency reply is never replaced
  by a timer confirmation.
- **Medication wording never comes back out.** "Take two pills at 8" is spoken
  as "take your medication"; the exact words go to the caregiver dashboard only.

---

## A8 — mascot redesign: porcelain sphere in a jade cradle (2026-10-28)

The owner supplied a reference image and asked for a **visual-only** replacement
of the abstract orb with a friendly **non-human** mascot: a porcelain sphere with
a soft face cradled in jade leaves. No audio, WebSocket, mic-gate, echo or
state-machine code was touched — the character is drawn inside `OrbCharacter`
and all voice logic stayed in `useVoiceSession`.

### Geometry (400×400 viewBox, matched to the reference)

| Part | Value |
|---|---|
| Sphere | `r 120` at `(200,190)` → **60%** of the component width; warm cream gradient `#FFFEFB → #F1E8D9`, low-right shade, hairline `#E7DCC7` edge at 0.35 |
| Brows | 31% down the sphere, `#8A6F57`, 3.6 units, round caps, **asymmetric** (inner ends slightly higher) |
| Eyes | 44% down, `r 12.25` (≈10% of the diameter), spacing ±20.8%, dark chocolate `#3A2819 → #5C4231`, white catchlight plus a faint lower glint |
| Mouth | 61% down, 38 units wide, `#8A6F57`, round caps, gentle smile |
| Cheeks | 57% down, `r 22` at ±60, `#F6C4B0` at 0.46 under a 9-unit blur |
| Bowl | As wide as the sphere, tips tucked to the silhouette at 69% height, front rim at ~89%, `#A2CDC0 → #82B3A4`, lit rim `#C2E4D5`, blurred inner lip shadow |
| Leaves | 4, two per side, sage `#B0C7AA → #96B096` (upper, larger) and `#A6BE9F → #90AA90` (lower, smaller); tips reach 1.33× the sphere diameter and 17%/59% down |
| Light | No hard outlines anywhere; one `feGaussianBlur` contact shadow at 0.04 |

Each part is its own `<g>`: `body`, `leaves-left`, `leaves-right`, `bowl`,
`brows`, `look`, `eyes`, `cheeks`, `mouth-swing`, `mouth-level`. Gradient and
filter ids are namespaced with `useId` because the dev preview renders many
avatars on one page and shared ids would make every tile inherit the first
instance's `--orb-color`.

### State behaviour (CSS only)

| State | Motion | Expression |
|---|---|---|
| `idle` | breathe 1 → 1.02 over 5 s, blink every ~6.5 s, glow pulse 6 s | neutral smile |
| `listening` | leaves open ∓7°, slate-blue glow | eyes widen 1.06, brows lift 2.5 |
| `thinking` | leaves sway ∓1.5–2° over 7 s, gaze drifts 5.5/−4.5 over 4.5 s, lavender glow | brows lift 1.5 |
| `speaking` | body pulse 1 → 1.012 over 0.9 s, mouth loop 0.85 s × tier, amber glow | brows lift 1 |
| `error` | **everything still** — no flash, no red on the character | same face |

Every duration is divided by `--anim-speed`, and the tier also dims
`--glow-opacity` and shrinks `--mouth-scale`, so Red is the *calmest* state.
The character's own palette never changes; only the halo takes state colour.
`prefers-reduced-motion` disables all motion and keeps the per-state expression.

### Verification

- `npx tsc -b` clean, `npm run lint` **9 warnings, none new**, `npm run test:pwa`
  **55/55**, `npm run build` clean.
- Browser checks at 412 px and in the dev preview: all five states apply the
  expected animation names, transforms and expressions; `error` has **zero**
  running animations; reduced motion leaves the expression and stops all motion.
- Screenshots of idle / listening / thinking / speaking / error and a Red-tier
  idle were reviewed against the reference before commit.

> The reference PNG itself is **not committed** — it was pasted into chat rather
> than saved to the repo. Drop it at `docs/mascot-reference.png` if it should
> live in-tree.

---

## Rejected Options (Record)

| Option | Reason |
|--------|--------|
| **2. Minimal Illustrated Character** | Requires maintaining SVG expression paths; higher drift risk toward "character" |
| **3. Lottie/Rive** | Adds 15–40KB dependency; asset pipeline; runtime color/speed control harder; asset design risk |

---

## Standing constraint

The avatar is output-only. No camera, no face tracking, no lip-sync tooling on
live replies, no second voice, and no tier that makes it look more alarmed.
