"""
push.py

Web Push key handling and public configuration for M5.

Step 3 is **storage and configuration only** — this module never sends a
notification. The private VAPID key lives in `backend/.env` (untracked); only
the public key is ever exposed, and only to the caregiver's own browser.

Two gates exist, both default OFF (owner decision 6):
- `SAHAARA_PUSH_ENABLED`  — backend send gate (read here)
- `VITE_PUSH_ENABLED`     — frontend build gate (read in the browser bundle)
"""

import base64
import os
from typing import Optional

TRUTHY = {"1", "true", "yes", "on"}


def _flag(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() in TRUTHY


def push_enabled() -> bool:
    """Backend send gate. Default OFF."""
    return _flag(os.environ.get("SAHAARA_PUSH_ENABLED", "false"))


def vapid_subject() -> str:
    """Contact address for the push service. Never a secret."""
    return os.environ.get("VAPID_SUBJECT", "mailto:caregiver@example.com").strip()


def vapid_private_key_pem() -> Optional[bytes]:
    """
    The private VAPID key as PEM bytes, or None.

    Stored in `.env` as one line with escaped newlines so `.env` stays a flat
    key/value file; both escaped and real newlines are accepted.
    """
    raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not raw:
        return None
    if "\n" not in raw and "\\n" in raw:
        raw = raw.replace("\\n", "\n")
    return raw.encode() if raw else None


def vapid_public_key() -> Optional[str]:
    """
    Base64url-encoded uncompressed public key (the value a browser passes as
    `applicationServerKey`), or None when no key is configured.

    Derived from the private key so the two can never drift; an explicit
    `VAPID_PUBLIC_KEY` env var wins if one is set.
    """
    override = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if override:
        return override

    pem = vapid_private_key_pem()
    if not pem:
        return None

    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        vapid = Vapid.from_pem(pem)
        raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    except Exception:
        # A malformed key must not take the app down: push simply stays
        # unconfigured, and no secret ever leaks into a response.
        return None


def public_push_config() -> dict:
    """
    The only push information that is safe to serve publicly.

    With the send gate OFF the public key is omitted entirely.
    """
    enabled = push_enabled()
    config: dict = {"push_enabled": enabled}
    if enabled:
        key = vapid_public_key()
        if key:
            config["vapid_public_key"] = key
    return config
