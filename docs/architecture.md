# Sahaara — Architecture

## Pipeline (per conversation turn)

```
Voice input
   ↓
Speech recognition (ASR)
   ↓
Intent + emotional-state detection
   ↓
Memory system (profile, routine, Memory Box, session notes)
   ↓
Safety / Policy engine  ← hard boundary before the LLM
   ↓
LLM (response generation)
   ↓
Voice output (TTS)
```

**In parallel:**
```
Conversation events → pattern detection → caregiver dashboard (alert only if meaningful)
```

### Why the policy engine sits before the LLM, not after
Guardrails (no diagnosis, no dosing advice, no impersonation, no fabricated events) are enforced as constraints going *into* generation, not as a filter cleaning up what comes out. Post-hoc filtering can still let a harmful framing leak through in tone even if the literal words are blocked; constraining the prompt itself is more reliable. Do not restructure this without discussion.

## Model Tiering (Nebius / Token Factory)
| Model | Used for |
|---|---|
| Nemotron Nano / Lightning | Fast turn-taking, routine conversation, low latency |
| Nemotron 3 Ultra | Harder judgment calls — is this distress pattern worth escalating, how to handle an ambiguous emotional moment |
| Nemotron 3 VoiceChat | Live voice loop, if Early Access is confirmed working day-one |
| Fallback: cascaded ASR → LLM → TTS | Used if VoiceChat is unavailable or unreliable |

Nebius Serverless: endpoints for the live voice loop, async jobs for the daily caregiver summary.

## Response Policy (applied every turn)
1. **Listen** for the emotion, not just the literal question
2. **Validate** it — acknowledge how they feel
3. **Ground** gently — orient to place/time/safety without being corrective
4. **Offer one small choice** — never an open-ended "what do you want to do"
5. **Escalate only when needed** — ask before contacting a caregiver, don't do it silently unless the person is unresponsive

Hard rule: respond to the emotional need, not the factual-accuracy contest. Never lie to comfort, never impersonate.

## Escalation Ladder
| Level | Trigger | AI does | Caregiver gets |
|---|---|---|---|
| 🟢 Green | Routine repeated question | Answer warmly, no mention of repetition | Nothing |
| 🟡 Yellow | Sustained anxiety, 10–15 min | Validate, ground, offer to call | Quiet daily-summary line |
| 🟠 Orange | Persistent distress or missed routine | Ask if they want a trusted contact called | Prompt to check in |
| 🔴 Red | Danger statement, no response | Direct to emergency support; contact configured emergency contact if consented | Urgent alert with context |

Framing rule: this is "notify a human when a pattern crosses a threshold" — never "detects falls" or "detects wandering." Those phrases imply clinical/regulated claims this product does not make.

## Memory System
- **This Is Me profile** — caregiver-populated and editable, never AI-guessed: name, family, life story, likes, avoid topics, daily routine
- **Memory Box** — short voice notes from family tied to specific triggers (e.g. a daughter's recorded hello, played when Mom asks about her)
- **Session notes** — short-term state for the current conversation (repetition tracking, mood trend)

## Vision/Video (roadmap only, not built this weekend)
If built later:
- Frame-sampled and on-demand only — never continuous recording or storage; only the interpretation is logged, never footage
- No facial recognition as an identity claim — "a person is present," not "this is confirmed to be Priya"
- No fall-detection framing — "unusual posture/inactivity → notify a human," never "fall detected"
- Never always-on by default; visible indicator whenever active