# Sahaara Avatar — Design Decision (A1)

**Date:** 2026-10-28  
**Decision:** Option 1 — Abstract Breathing Orb + Soft Face  
**Status:** Approved, proceeding to A2

---

## Selected Style: Abstract Breathing Orb + Soft Face

The avatar is an evolution of the existing `VoiceOrb` — same 130px circular form, same low-arousal color palette, with subtle "face" elements added:

- **Eyes:** Two soft glow points (small radial gradients) at ~35%/65% horizontal, 35% vertical
- **Mouth:** A horizontal line (or shallow arc) at ~65% vertical that opens/closes with audio level

No anatomical proportions, no human features beyond abstract suggestion.

---

## State Visual Behaviors

| State | Color (from VoiceOrb) | Eyes | Mouth | Motion |
|-------|----------------------|------|-------|--------|
| **Idle** | `#C9C2B8` (stone) | Soft, resting (opacity 0.35) | Short closed line | Very slow glow pulse |
| **Listening** | `#7C93A8` (slate blue) | Brighter (opacity 0.75), steady | Short closed line | Slow 2–3° tilt, 4s period |
| **Thinking** | `#A98FC7` (lavender) | Gentle blink (opacity 0.75→0.2→0.75, 3s) | Short closed line | Slow pulse (1.8s, matches orb) |
| **Speaking** | `#E8A85C` (warm amber) | Bright (opacity 0.75), steady | **Amplitude-driven** (0–1 from AnalyserNode) | Mouth opens/closes, subtle glow pulse |
| **Error** | `#C2716B` (muted clay) | Bright (opacity 0.75) | Short closed line | Same calm pulse |

Eye opacity values were raised from the A2 draft (0.15 idle / 0.6 active) after
looking at the rendered avatar: at those values the "soft face" was invisible at
130 px and the component read as a plain sphere. The eye gradient was also
changed from the state colour (near-white where the eyes sit) to a neutral core
so the points stay visible on every state colour.

---

## Tier Calmness Mapping (Orange/Red = Calmer, Never Urgent)

Live tier comes from the same SSE/WS `tier_change` stream the caregiver ladder
uses: `EscalationLadder` pushes it into `VoiceStateContext`, `AshaVisual` passes
it to `SahaaraAvatar`. It is also seeded from `GET /escalation/status` on load
and on every socket open, so a reload mid-escalation does not show green.

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

The mouth is a single 46-unit line in the 130×130 viewBox. It rests at 10 units
(a calm, closed line) and opens symmetrically to `10 + 36 × level × mouth-scale`
using `stroke-dasharray` + a centring `stroke-dashoffset` — so it grows from the
middle rather than sliding in from one side.

---

## Non-Human Guarantees (Hard Constraints)

1. **Geometry only:** Circles, lines, radial gradients. No paths resembling eyes/nose/mouth in human arrangement.
2. **Color palette fixed:** Only the 4 VoiceOrb colors + transparency. No skin tones, no warm pinks/reds that suggest flesh.
3. **Proportions abstract:** Eye points at 35%/65% horizontal (not human 30%/70%), mouth at 65% vertical (not human 70%+).
4. **No animation suggests personality:** No winking, smiling, frowning, surprise. Only state-driven: attentive, processing, speaking.

---

## Technical Approach

- **Inline SVG** in `SahaaraAvatar.tsx` — no external assets, no build step
- **CSS custom properties** for all dynamic values (`--orb-color`, `--anim-speed`, `--mouth-open`, `--glow-opacity`, `--eye-opacity`)
- **Single `requestAnimationFrame` loop** for audio-reactive mouth (A4) — reads from `AnalyserNode`, writes `--mouth-open`
- **`prefers-reduced-motion`** respected via existing CSS pattern (disable animations, color-only state)
- **Zero dependencies** — matches project constraint

---

## Files to Create (A2)

```
frontend/src/
├── SahaaraAvatar.tsx      # Pure presentational component
├── SahaaraAvatar.css      # Styles + animations
```

**Props:**
```ts
interface SahaaraAvatarProps {
  state: 'idle' | 'listening' | 'thinking' | 'speaking';
  level?: number;          // 0–1 audio amplitude (speaking only)
  tier?: 'green' | 'yellow' | 'orange' | 'red';  // defaults to 'green'
}
```

**Dev preview:** `/avatar-preview` route (behind `import.meta.env.DEV`) with state/tier/level sliders.

---

## Phase Status

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **A1** | This decision memo | ✅ Done |
| **A2** | `SahaaraAvatar.tsx` / `.css` + dev preview | ✅ Done |
| **A3** | `VoiceStateContext` + Orb/Avatar toggle + ErrorBoundary fallback | ✅ Done |
| **A4** | Audio-reactive mouth (AnalyserNode on the playback path, 50 ms attack / 150 ms release) | ✅ Done, device session still pending |
| **A5** | Tier calmness wired from the live escalation stream | ✅ Done (browser-verified) |
| **A6** | Accessibility/performance hardening | ⬜ Not started |
| **A7** | Tests + docs | ⬜ Not started |

### Wiring note (found while wiring A5)

`AppContent` rendered `<VoiceStateProvider>` inside its own JSX, so a
`useVoiceStateReadOnly()` call in `AppContent` was **outside** the provider and
always returned the defaults — the avatar was permanently idle/green. The
orb/avatar section was moved into a child component (`AshaVisual`) that sits
inside the provider. The context was also split into three parts (state+ tier,
level, stable actions) so the 60 fps amplitude updates re-render only the
avatar, not the whole caregiver dashboard.

## Rejected Options (Record)

| Option | Reason |
|--------|--------|
| **2. Minimal Illustrated Character** | Requires maintaining SVG expression paths; higher drift risk toward "character" |
| **3. Lottie/Rive** | Adds 15–40KB dependency; asset pipeline; runtime color/speed control harder; asset design risk |

---

## Next Phase: A2

Build `SahaaraAvatar.tsx` as a pure presentational component with dev preview. No audio wiring, no state plumbing — just `props in → SVG out`.