# PWA & Push Notification Implementation Plan - Sahaara

## Overview
Implement a Progressive Web App with Web Push notifications for Android demo. The backend already has physical-distress detection (urgent → Red, general → Orange). This plan covers the frontend PWA shell, service worker, push subscription flow, and backend integration.

## Current State
- Backend: Physical-distress detection implemented (urgent → Red, general → Orange)
- Backend: Escalation alerts with `PHYSICAL_SYMPTOM` event logging
- Frontend: React + Vite + TypeScript, no PWA setup yet
- Demo target: Android phone (Chrome)

## Implementation Steps

### Step 1: PWA Manifest & Icons (30 min)
**Files to create:**
- `frontend/public/manifest.json` - Web App Manifest
- `frontend/public/icons/icon-192.png` - 192x192 icon
- `frontend/public/icons/icon-512.png` - 512x512 icon
- Update `index.html` to reference manifest

**Manifest requirements:**
```json
{
  "name": "Sahaara",
  "short_name": "Sahaara",
  "description": "Consent-based AI companion for dementia care",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#faf9f6",
  "theme_color": "#7C93A8",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

### Step 2: Service Worker (45 min)
**Files to create:**
- `frontend/public/sw.js` - Service Worker
- Register SW in `main.tsx`

**Service Worker responsibilities:**
- Handle push events (show notification)
- Handle notification click (open dashboard)
- Cache static assets for offline
- Handle push subscription updates

### Step 3: Push Subscription UI (60 min)
**Components to add:**
- "Enable Alerts" button in caregiver dashboard
- VAPID key generation (backend)
- Subscription storage (IndexedDB + backend sync)
- Permission handling (user gesture required)

**UI Flow:**
1. Caregiver opens dashboard
2. Sees "Enable Alerts" banner if not subscribed
3. Taps button → permission prompt → subscription
3. Backend stores subscription + VAPID keys

### Step 4: Backend Push Integration (60 min)
**Backend changes:**
- VAPID key generation (`py_vapid` or `cryptography`)
- Subscription storage (JSON file or DB)
- Push sending via `pywebpush`
- Alert → push trigger with cooldown
- `ALERT_SENT` event logging

**Push payload:**
```json
{
  "title": "Asha needs a check-in",
  "body": "Asha said: \"I can't breathe.\"",
  "data": { "url": "/dashboard#event-123", "eventId": "123" },
  "tag": "sahaara-alert"
}
```

### Step 5: Android HTTPS Tunnel (15 min)
- Use Cloudflare Tunnel or ngrok for HTTPS
- Test "Add to Home Screen" on Android
- Test push with screen locked

## Files to Create/Modify

| File | Purpose |
|------|---------|
| `frontend/public/manifest.json` | Web App Manifest |
| `frontend/public/icons/icon-192.png` | 192x192 icon |
| `frontend/public/icons/icon-512.png` | 512x512 icon |
| `frontend/public/sw.js` | Service Worker |
| `frontend/src/main.tsx` | Register SW |
| `frontend/src/hooks/usePushNotifications.ts` | Push subscription hook |
| `frontend/src/components/PushEnableBanner.tsx` | Enable Alerts UI |
| `backend/push.py` | Push notification logic |
| `backend/main.py` | Push endpoints + alert→push trigger |
| `backend/vapid_keys.py` | VAPID key management |

## Android Testing Checklist
- [ ] HTTPS tunnel works (ngrok/Cloudflare)
- [ ] "Add to Home Screen" prompt appears
- [ ] App opens in standalone mode
- [ ] Push permission prompt works
- [ ] Notification appears when screen locked
- [ ] Tapping notification opens dashboard
- [ ] Physical distress phrase triggers push

## VAPID Key Generation (Backend)
```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

# Save private key for sending pushes
# Encode public key for frontend (base64url)
```

## Push Cooldown Rules
- Per-tier cooldown: 10 minutes (prevents spam during episode)
- Max 3 pushes per hour per caregiver
- Only trigger on tier change (Green→Yellow, Yellow→Orange, any→Red)
- Physical distress: immediate, bypasses normal cooldown if new event

## Security Considerations
- VAPID private key never exposed to frontend
- Push payloads minimal (no PII in lock screen)
- Subscription endpoint validated before storage
- Expired subscriptions auto-removed on 410/404

## Acceptance Criteria
- [ ] PWA installs on Android Chrome
- [ ] "Enable Alerts" works with one tap
- [ ] Physical distress phrase triggers push within 2s
- [ ] Notification shows correct title/body
- [ ] Tap notification → opens dashboard
- [ ] Dashboard shows `ALERT_SENT` event
- [ ] Cooldown prevents spam
- [ ] Works with screen locked

## Testing Commands
```bash
# Frontend dev server
cd frontend && npm run dev

# Backend
cd backend && python -m uvicorn main:app --reload

# HTTPS tunnel
npx ngrok http 5173  # or cloudflared tunnel --url http://localhost:5173
```

## Risk Mitigation
- **Fish Audio credits exhausted**: Use Cartesia (already reverted)
- **Android push unreliable**: Keep in-app dashboard as primary
- **VAPID key rotation**: Plan for key rotation in production
- **Expired subscriptions**: Auto-cleanup on 410 response