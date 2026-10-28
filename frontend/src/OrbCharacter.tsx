/* OrbCharacter — the single visible mascot: a porcelain sphere with a soft face
 * cradled in jade leaves.
 *
 * Rules this drawing must keep (see docs/avatar_design.md):
 *  - Friendly NON-HUMAN mascot. No skin tones, no hair, no likeness of Priya,
 *    Rohan or Vikram. It is Sahaara, not a person.
 *  - The character's own palette (cream / jade / brown / peach) is CONSTANT in
 *    every state. Only the soft glow ring takes the state colour, so a tired or
 *    worried moment never repaints the mascot itself.
 *  - No text inside the character, no flashing, no red character styling.
 *  - Every part is its own <g> so CSS can move one thing without touching the
 *    rest: leaves-left, leaves-right, body, bowl, brows, eyes, cheeks, mouth.
 *
 * Pure presentation: no state, no effects, no timers. `data-state` on the
 * wrapping <svg> drives every animation from OrbCharacter.css.
 *
 * Geometry was matched to docs/mascot-reference.png (400×400 viewBox):
 *   sphere r=120 at (200,190), i.e. 60% of the component width;
 *   brows 31% down the sphere, eyes 44%, mouth 61%, cheeks 57%;
 *   leaves emerge at the equator and reach past the bowl on both sides.
 */
import { useId } from "react";

import "./OrbCharacter.css";

export interface OrbCharacterProps {
  /** Mirrors the SVG's data-state; used as a root marker for CSS + tests. */
  state: "idle" | "listening" | "thinking" | "speaking" | "error";
  /** 0–1 real playback amplitude. Adds a little openness to the mouth. */
  level?: number;
}

/* Long, slender petals. Written out for the left side and mirrored by hand so
 * each side can rotate around its own base without a mirrored transform. */
const LEAF_UPPER_LEFT = "M 114 214 Q 40 206 46 112 Q 114 148 114 214 Z";
const LEAF_LOWER_LEFT = "M 116 252 Q 44 266 40 212 Q 98 236 116 252 Z";
const LEAF_UPPER_RIGHT = "M 286 214 Q 360 206 354 112 Q 286 148 286 214 Z";
const LEAF_LOWER_RIGHT = "M 284 252 Q 356 266 360 212 Q 302 236 284 252 Z";

/* Bowl: as wide as the sphere, tips curling just past its silhouette, dipping
 * a little below the sphere so it reads as a cradle from the front. */
const BOWL = "M 86 232 C 114 360 286 360 314 232 C 286 296 114 296 86 232 Z";
const BOWL_RIM = "M 94 240 C 128 290 272 290 306 240";
const BOWL_INNER_SHADOW = "M 98 248 C 130 298 270 298 302 248";

export default function OrbCharacter({ state, level = 0 }: OrbCharacterProps) {
  // Gradients live in <defs> and are referenced by URL. Two avatars on one page
  // (the dev preview renders many) would otherwise share the first instance's
  // gradient and inherit its state colour, so every id is namespaced.
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const id = (name: string) => `oc-${uid}-${name}`;
  const fill = (name: string) => `url(#${id(name)})`;

  const clamped = Math.max(0, Math.min(1, level));
  // Amplitude only adds a little openness on top of the gentle speaking loop;
  // at silence the face is exactly the reference smile.
  const mouthLevel = 1 + clamped * 0.35;

  return (
    <g className="oc-root" data-state={state}>
      <defs>
        {/* Porcelain body: near-white crown, warm ivory toward the edge. */}
        <radialGradient id={id("body")} cx="42%" cy="32%" r="74%">
          <stop offset="0%" stopColor="#FFFEFB" />
          <stop offset="52%" stopColor="#FCF8EF" />
          <stop offset="100%" stopColor="#F1E8D9" />
        </radialGradient>

        {/* Upper-left gloss. */}
        <radialGradient id={id("highlight")} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.80" />
          <stop offset="100%" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>

        {/* Faint warm shading low and to the right. */}
        <radialGradient id={id("shade")} cx="66%" cy="74%" r="60%">
          <stop offset="0%" stopColor="#DFCDB2" stopOpacity="0.34" />
          <stop offset="100%" stopColor="#DFCDB2" stopOpacity="0" />
        </radialGradient>

        {/* Jade cradle — sage leaves, slightly cooler bowl. */}
        <linearGradient id={id("bowl")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#A2CDC0" />
          <stop offset="100%" stopColor="#82B3A4" />
        </linearGradient>
        <linearGradient id={id("leaf-upper")} x1="0" y1="1" x2="0.35" y2="0">
          <stop offset="0%" stopColor="#96B096" />
          <stop offset="100%" stopColor="#B0C7AA" />
        </linearGradient>
        <linearGradient id={id("leaf-lower")} x1="0" y1="1" x2="0.45" y2="0">
          <stop offset="0%" stopColor="#90AA90" />
          <stop offset="100%" stopColor="#A6BE9F" />
        </linearGradient>

        {/* Eyes: dark chocolate, a touch lighter at the lower rim. */}
        <radialGradient id={id("eye")} cx="42%" cy="34%" r="74%">
          <stop offset="0%" stopColor="#3A2819" />
          <stop offset="70%" stopColor="#4A3222" />
          <stop offset="100%" stopColor="#5C4231" />
        </radialGradient>

        {/* Glow ring — the ONLY place the state colour appears. */}
        <radialGradient id={id("glow")} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--orb-color, #c9c2b8)" stopOpacity="0" />
          <stop offset="62%" stopColor="var(--orb-color, #c9c2b8)" stopOpacity="calc(var(--glow-opacity, 0.4) * 0.45)" />
          <stop offset="70%" stopColor="var(--orb-color, #c9c2b8)" stopOpacity="calc(var(--glow-opacity, 0.4) * 0.75)" />
          <stop offset="100%" stopColor="var(--orb-color, #c9c2b8)" stopOpacity="0" />
        </radialGradient>

        <filter id={id("blush")} x="-70%" y="-70%" width="240%" height="240%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <filter id={id("soft-shadow")} x="-40%" y="-160%" width="180%" height="420%">
          <feGaussianBlur stdDeviation="7" />
        </filter>
        <filter id={id("lip-blur")} x="-20%" y="-60%" width="140%" height="260%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      {/* State tint, behind everything and never on the character. */}
      <circle className="oc-glow" cx="200" cy="190" r="168" fill={fill("glow")} />

      {/* A whisper of a contact shadow under the bowl — the reference has
          almost none, and nothing else casts one. */}
      <ellipse
        className="oc-shadow"
        cx="200"
        cy="330"
        rx="88"
        ry="7"
        fill="#6B5B45"
        opacity="0.04"
        filter={`url(#${id("soft-shadow")})`}
      />

      {/* Leaves sit BEHIND the sphere, two per side, emerging near the equator. */}
      <g className="oc-leaves oc-leaves-left">
        <path d={LEAF_UPPER_LEFT} fill={fill("leaf-upper")} />
        <path d={LEAF_LOWER_LEFT} fill={fill("leaf-lower")} />
      </g>
      <g className="oc-leaves oc-leaves-right">
        <path d={LEAF_UPPER_RIGHT} fill={fill("leaf-upper")} />
        <path d={LEAF_LOWER_RIGHT} fill={fill("leaf-lower")} />
      </g>

      {/* The sphere. */}
      <g className="oc-body">
        <circle cx="200" cy="190" r="120" fill={fill("body")} />
        <circle cx="200" cy="190" r="120" fill={fill("shade")} />
        <circle cx="158" cy="132" r="82" fill={fill("highlight")} />
        {/* A hairline edge keeps the cream sphere readable on an off-white
            page without ever reading as an outline. */}
        <circle
          cx="200"
          cy="190"
          r="120"
          fill="none"
          stroke="#E7DCC7"
          strokeWidth="1.4"
          strokeOpacity="0.35"
        />
      </g>

      {/* Face — upper-middle of the sphere, exactly as in the reference. */}
      <g className="oc-face">
        <g className="oc-brows">
          <path
            d="M 128 147 Q 145 132 163 134"
            fill="none"
            stroke="#8A6F57"
            strokeWidth="3.6"
            strokeLinecap="round"
          />
          <path
            d="M 272 144 Q 256 130 237 132"
            fill="none"
            stroke="#8A6F57"
            strokeWidth="3.6"
            strokeLinecap="round"
          />
        </g>

        {/* `.oc-look` shifts the gaze; `.oc-eyes` blinks. Separate groups so
            the two movements never fight over one transform. */}
        <g className="oc-look">
          <g className="oc-eyes">
            <circle cx="150" cy="177" r="12.25" fill={fill("eye")} />
            <circle cx="250" cy="177" r="12.25" fill={fill("eye")} />
            {/* Catchlights, upper-left of each eye. */}
            <circle cx="145.2" cy="171.8" r="3.8" fill="#FFFFFF" fillOpacity="0.95" />
            <circle cx="245.2" cy="171.8" r="3.8" fill="#FFFFFF" fillOpacity="0.95" />
            <circle cx="154" cy="182" r="1.3" fill="#FFFFFF" fillOpacity="0.28" />
            <circle cx="254" cy="182" r="1.3" fill="#FFFFFF" fillOpacity="0.28" />
          </g>
        </g>

        <g className="oc-cheeks" filter={`url(#${id("blush")})`}>
          <circle cx="140" cy="204" r="22" fill="#F6C4B0" fillOpacity="0.46" />
          <circle cx="260" cy="204" r="22" fill="#F6C4B0" fillOpacity="0.46" />
        </g>

        {/* Two nested groups so the gentle speaking loop and the real
            amplitude never fight over one transform. */}
        <g className="oc-mouth-swing">
          <g
            className="oc-mouth-level"
            style={{ transform: `scaleY(${mouthLevel})`, transformOrigin: "200px 214px" }}
          >
            <path
              d="M 181 213 Q 200 224 219 213"
              fill="none"
              stroke="#8A6F57"
              strokeWidth="4.5"
              strokeLinecap="round"
            />
          </g>
        </g>
      </g>

      {/* Where the sphere meets the bowl it sits in a little shade, which is
          what makes the cradle read as depth instead of a flat cut-out. */}
      <ellipse
        className="oc-contact"
        cx="200"
        cy="288"
        rx="92"
        ry="26"
        fill="#D8C5AB"
        opacity="0.20"
        filter={`url(#${id("soft-shadow")})`}
      />

      {/* Bowl last: it cradles the sphere from the front. */}
      <g className="oc-bowl">
        <path d={BOWL} fill={fill("bowl")} />
        {/* Shadow just under the lip. */}
        <path
          d={BOWL_INNER_SHADOW}
          fill="none"
          stroke="#5E8F82"
          strokeWidth="7"
          strokeOpacity="0.16"
          strokeLinecap="round"
          filter={`url(#${id("lip-blur")})`}
        />
        {/* Lit rim. */}
        <path
          d={BOWL_RIM}
          fill="none"
          stroke="#C2E4D5"
          strokeWidth="5.5"
          strokeOpacity="0.72"
          strokeLinecap="round"
        />
      </g>
    </g>
  );
}
