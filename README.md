# Sahaara

Consent-based AI voice companion for people living with dementia and their family caregivers.

## Quick start

```bash
# Backend
cd backend && .venv/bin/python -m uvicorn main:app --reload

# Frontend
cd frontend && npm run dev
```

## Caregiver phone alerts (Web Push)

Android Chrome only. iOS is not tested and not claimed.

Phone alerts are **off by default** and need two gates plus a key pair:

```bash
# 1. Generate VAPID keys once, then paste the printed lines into backend/.env
cd backend && .venv/bin/python scripts/generate_vapid_keys.py

# 2. Turn both gates on
#    backend/.env      SAHAARA_PUSH_ENABLED=true
#    frontend/.env.local  VITE_PUSH_ENABLED=true

# 3. Build the frontend with the build gate on, then open it on the phone
cd frontend && VITE_PUSH_ENABLED=true npm run build
```

Tap **Enable alerts** in the dashboard header. On Android the app must be
installed (Chrome menu → *Add to Home screen*): a normal browser tab does not
keep the notification permission, so the service worker cannot show anything.

What a notification says: **"Sahaara" / "Asha needs a check-in"** — a fixed
sentence. No conversation text, no names, no symptoms, and no tier ever leave
the server in a push payload, and the service worker ignores the payload and
renders the fixed body anyway. Tapping it opens that alert on the dashboard.

How to read the record: **"accepted by the push service" means the push service
answered 2xx — never that a phone displayed it.** The dashboard stays the
record. Android may delay or block notifications (battery saver, Doze, OEM
battery managers); the alert is still on the dashboard.

Only an **upward tier move** (Green → Yellow → Orange → Red) rings a phone.
Green, consent outcomes, fallback notices and de-escalations stay on the
dashboard. Cooldowns are per profile and persisted: Yellow 30 min, Orange
10 min, Red 2 min, and an escalation always sends even inside the lower tier's
window. `SAHAARA_PUSH_COOLDOWN_SECONDS` overrides all three (`0` disables).

Troubleshooting:

| Symptom | Cause |
|---|---|
| Button says *Alerts blocked* | Notification permission for this site is denied. Chrome does not ask again; reset it in the site settings. |
| Button says *Alerts aren't available in this browser* | No `PushManager` (insecure origin, or not Chrome). Use `https://` or `127.0.0.1`. |
| Status shows 0 active subscriptions | The phone never subscribed, or the subscription was revoked after a 404/410. Tap *Enable alerts* again. |
| `config` shows `push_enabled: false` | `SAHAARA_PUSH_ENABLED` is off, so `/push/subscribe` answers 409 and nothing is stored. |
| Notification never appears | Check the dashboard record first. Battery saver / Doze can hold a push for minutes; a normal browser tab cannot show it at all. |

Rotating the VAPID keys invalidates every existing subscription — each phone
must press *Enable alerts* again.

## Docs

- [`AGENTS.md`](AGENTS.md) — full project handoff and changelog
- [`docs/architecture.md`](docs/architecture.md) — pipeline, model tiering, escalation
- [`docs/guardrails.md`](docs/guardrails.md) — safety rules and test cases
- [`overview.md`](overview.md) — demo script and persona details