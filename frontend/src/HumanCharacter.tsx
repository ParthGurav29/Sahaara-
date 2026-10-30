/* HumanCharacter — Sahaara's avatar as a gentle, stylized human portrait.
 *
 * Owner decision (2026-10-31): switch from the non-human porcelain mascot to a
 * human-style character. Recorded in agents.md + docs/avatar_design.md.
 *
 * Deliberately stylized (flat-vector + soft gradients) — NOT a photographic or
 * named person, and never a likeness of Priya / Rohan / Vikram. Warm, calm,
 * low-arousal palette from the reference: navy backdrop, golden halo, mint scarf,
 * peach skin, dark hair.
 *
 * Motion contract (kept from the mascot — see docs/avatar_design.md):
 *  - `data-state` on the wrapping <svg> drives every animation from CSS.
 *  - `--mouth-open` / `--mouth-wide` are written by SahaaraAvatar from the real
 *    TTS analyser, so the mouth lip-syncs (A9). `--mouth-scale` is the tier.
 *  - States: idle = breathe + blink; listening = (the reactive ring lives in
 *    SahaaraAvatar) + eyes widen; thinking = gaze drift + brows lift;
 *    speaking = soft pulse + mouth follows audio; error = still.
 *  - Tier calmness: `--anim-speed` slows/dims the motion; Red never speeds up.
 *
 * Pure presentation: no state, no effects, no timers, no hooks except useId.
 */
import { useId } from "react";

import "./HumanCharacter.css";

export interface HumanCharacterProps {
  state: "idle" | "listening" | "thinking" | "speaking" | "error";
}

const SKIN_SHADE = "#EEBD99";

export default function HumanCharacter({ state }: HumanCharacterProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const id = (name: string) => `hc-${uid}-${name}`;
  const fill = (name: string) => `url(#${id(name)})`;

  return (
    <g className="hc-root" data-state={state}>
      <defs>
        {/* Navy-indigo backdrop: slightly lighter at the centre, vignetted. */}
        <radialGradient id={id("backdrop")} cx="50%" cy="42%" r="72%">
          <stop offset="0%" stopColor="#2B2D55" />
          <stop offset="55%" stopColor="#222445" />
          <stop offset="100%" stopColor="#1A1C3A" />
        </radialGradient>

        {/* Golden-cream halo behind the head and shoulders. */}
        <radialGradient id={id("halo")} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#FFF3D6" stopOpacity="0.92" />
          <stop offset="42%" stopColor="#FFE9B8" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#FFE9B8" stopOpacity="0" />
        </radialGradient>

        {/* Warm skin. */}
        <radialGradient id={id("skin")} cx="42%" cy="34%" r="74%">
          <stop offset="0%" stopColor="#F8D2B2" />
          <stop offset="100%" stopColor="#F2C29F" />
        </radialGradient>

        {/* Hair: dark brown-black with a faint warm sheen. */}
        <radialGradient id={id("hair")} cx="42%" cy="30%" r="80%">
          <stop offset="0%" stopColor="#44302A" />
          <stop offset="60%" stopColor="#33221C" />
          <stop offset="100%" stopColor="#241712" />
        </radialGradient>

        {/* Mint-green scarf/shawl. */}
        <linearGradient id={id("scarf")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#9CD4B6" />
          <stop offset="100%" stopColor="#78B99B" />
        </linearGradient>

        {/* Cream wrap top, fading to backdrop at the bottom edge. */}
        <linearGradient id={id("shirt")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#F6F0E2" />
          <stop offset="60%" stopColor="#EDE4CF" />
          <stop offset="100%" stopColor="#C9C2C4" stopOpacity="0.1" />
        </linearGradient>

        {/* Eyes: dark chocolate with a lit lower rim. */}
        <radialGradient id={id("eye")} cx="42%" cy="34%" r="74%">
          <stop offset="0%" stopColor="#33200F" />
          <stop offset="78%" stopColor="#43291A" />
          <stop offset="100%" stopColor="#5A3A26" />
        </radialGradient>

        <filter id={id("blush")} x="-70%" y="-70%" width="240%" height="240%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <filter id={id("halo-soft")} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="14" />
        </filter>
      </defs>

      {/* ── Background medallion + halo + orbit ring ─────────────────────── */}
      <circle className="hc-backdrop" cx="200" cy="200" r="200" fill={fill("backdrop")} />
      <circle
        className="hc-halo"
        cx="200"
        cy="150"
        r="118"
        fill={fill("halo")}
        filter={`url(#${id("halo-soft")})`}
      />
      {/* The pale orbit line from the reference: centered on the head, ~1.4× its
          width, faint and partly lost in the glow. */}
      <circle className="hc-orbit" cx="200" cy="150" r="86" fill="none" />

      {/* ── Torso + clothing (behind the head) ───────────────────────────── */}
      <g className="hc-torso">
        {/* Neck. */}
        <rect x="184" y="186" width="32" height="42" rx="14" fill={SKIN_SHADE} />
        {/* Cream collared wrap top, shoulders to the bottom edge, fading out. */}
        <path
          className="hc-shirt"
          d="M 92 400 C 92 320 112 270 156 258 Q 200 250 244 258 C 288 270 308 320 308 400 Z"
          fill={fill("shirt")}
        />
        {/* Crossed neckline. */}
        <path d="M 184 204 L 158 258" stroke="#DCCFB6" strokeWidth="4" strokeLinecap="round" fill="none" />
        <path d="M 216 204 L 242 258" stroke="#DCCFB6" strokeWidth="4" strokeLinecap="round" fill="none" />
        {/* Mint scarf/shawl draped across the chest. */}
        <path
          className="hc-scarf"
          d="M 106 286 Q 104 266 134 268 Q 150 290 184 290 Q 200 278 216 290 Q 250 290 266 268 Q 296 266 294 286 Q 272 322 200 324 Q 128 322 106 286 Z"
          fill={fill("scarf")}
        />
        <path d="M 126 283 Q 160 304 200 306" stroke="#5E9B80" strokeWidth="3" strokeLinecap="round" fill="none" strokeOpacity="0.4" />
        <path d="M 274 283 Q 240 304 200 306" stroke="#5E9B80" strokeWidth="3" strokeLinecap="round" fill="none" strokeOpacity="0.4" />
      </g>

      {/* ── Ears (behind the face) ───────────────────────────────────────── */}
      <ellipse className="hc-ears" cx="140" cy="152" rx="10" ry="14" fill={SKIN_SHADE} />
      <ellipse className="hc-ears" cx="260" cy="152" rx="10" ry="14" fill={SKIN_SHADE} />

      {/* ── Head ─────────────────────────────────────────────────────────── */}
      <g className="hc-head">
        {/* Face: round and soft. */}
        <ellipse cx="200" cy="150" rx="60" ry="66" fill={fill("skin")} />

        {/* Blush. */}
        <g className="hc-cheeks" filter={`url(#${id("blush")})`}>
          <circle cx="163" cy="172" r="17" fill="#F4A98F" opacity="0.5" />
          <circle cx="237" cy="172" r="17" fill="#F4A98F" opacity="0.5" />
        </g>

        {/* Brows — thin, softly arched, set high. */}
        <g className="hc-brows">
          <path d="M 165 128 Q 175 118 185 119" fill="none" stroke="#3A2418" strokeWidth="3" strokeLinecap="round" />
          <path d="M 215 119 Q 225 118 235 128" fill="none" stroke="#3A2418" strokeWidth="3" strokeLinecap="round" />
        </g>

        {/* Eyes — large, round, glossy. `.hc-look` drifts the gaze, `.hc-eyes`
            blinks, so they are separate groups. */}
        <g className="hc-look">
          <g className="hc-eyes">
            <circle cx="173" cy="152" r="13" fill={fill("eye")} />
            <circle cx="227" cy="152" r="13" fill={fill("eye")} />
            {/* Upper lash line with a small wing at the outer corner. */}
            <path d="M 160 149 Q 173 141 187 146 L 190 148" fill="none" stroke="#241510" strokeWidth="2.2" strokeLinecap="round" />
            <path d="M 240 149 Q 227 141 213 146 L 210 148" fill="none" stroke="#241510" strokeWidth="2.2" strokeLinecap="round" />
            {/* Catchlights. */}
            <circle cx="168.5" cy="146.5" r="4" fill="#FFFFFF" fillOpacity="0.95" />
            <circle cx="222.5" cy="146.5" r="4" fill="#FFFFFF" fillOpacity="0.95" />
            <circle cx="178" cy="157" r="1.6" fill="#FFFFFF" fillOpacity="0.28" />
            <circle cx="232" cy="157" r="1.6" fill="#FFFFFF" fillOpacity="0.28" />
          </g>
        </g>

        {/* Nose — small and simple. */}
        <path d="M 200 162 Q 200 170 196 172" fill="none" stroke="#DEAE8A" strokeWidth="2.6" strokeLinecap="round" />

        {/* Mouth — closed smile; the opening behind it lip-syncs to the TTS. */}
        <g className="hc-mouth">
          <path
            className="hc-mouth-open"
            d="M 182 186 Q 200 204 218 186 Q 200 246 182 186 Z"
            fill="#8A4A45"
          />
          <path d="M 181 185 Q 200 195 219 185" fill="none" stroke="#BC7568" strokeWidth="4" strokeLinecap="round" />
          <path d="M 184 187 Q 200 194 216 187" fill="none" stroke="#D98A84" strokeWidth="3" strokeLinecap="round" strokeOpacity="0.9" />
        </g>
      </g>

      {/* ── Hair (frames the face) ────────────────────────────────────────── */}
      <g className="hc-hair">
        {/* Hairline cap over the top of the head. */}
        <path
          d="M 141 132 C 139 84 164 56 200 56 C 236 56 261 84 259 132 C 250 102 236 88 200 88 C 164 88 150 102 141 132 Z"
          fill={fill("hair")}
        />
        {/* Off-centre part. */}
        <path d="M 196 88 Q 191 96 186 108" stroke="#1A0F0B" strokeWidth="2" strokeLinecap="round" fill="none" strokeOpacity="0.5" />
        {/* High messy bun. */}
        <circle cx="200" cy="72" r="25" fill={fill("hair")} />
        <path d="M 179 60 Q 200 44 224 62" fill="none" stroke={fill("hair")} strokeWidth="7" strokeLinecap="round" />
        <path d="M 188 52 Q 200 42 212 50" fill="none" stroke="#4A3630" strokeWidth="4" strokeLinecap="round" />
        {/* Side tendrils framing the face. */}
        <path className="hc-tendril" d="M 141 118 C 133 148 140 172 148 198" fill="none" stroke={fill("hair")} strokeWidth="9" strokeLinecap="round" />
        <path className="hc-tendril" d="M 259 118 C 267 148 260 172 252 198" fill="none" stroke={fill("hair")} strokeWidth="9" strokeLinecap="round" />
        {/* Flyaways. */}
        <path d="M 168 62 Q 160 52 154 54" fill="none" stroke="#3A2B26" strokeWidth="2" strokeLinecap="round" strokeOpacity="0.7" />
        <path d="M 236 64 Q 246 52 250 56" fill="none" stroke="#3A2B26" strokeWidth="2" strokeLinecap="round" strokeOpacity="0.7" />
      </g>
    </g>
  );
}