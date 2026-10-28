"""
Generate the Web Push VAPID key pair for this server.

    cd backend && .venv/bin/python scripts/generate_vapid_keys.py

Prints the three lines to paste into `backend/.env` (which git ignores):

    VAPID_PRIVATE_KEY=<one-line PEM with escaped newlines>
    VAPID_SUBJECT=mailto:you@example.com
    VAPID_PUBLIC_KEY=<uncompressed P-256 point, base64url>

Why a script: the private key is a secret. Hand-copying a PEM into a single
line is easy to corrupt, and a corrupted key makes every send fail with
`no_vapid_key` (the server degrades instead of crashing, but nothing reaches a
phone). This writes the exact form `push.vapid_private_key_pem()` expects.

Options:
    --subject mailto:...   set the contact string (default: mailto:caregiver@example.com)
    --quiet                print only the three KEY=VALUE lines (for scripting)

The keys are printed, never written to disk here — redirect only into a file you
intend to keep secret, e.g.:

    .venv/bin/python scripts/generate_vapid_keys.py >> .env
"""

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def generate(subject: str) -> dict:
    """A fresh P-256 VAPID key pair in the exact shapes `.env` needs."""
    vapid = Vapid()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    public_raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "VAPID_PRIVATE_KEY": private_pem.replace("\n", "\\n").strip(),
        "VAPID_SUBJECT": subject,
        "VAPID_PUBLIC_KEY": base64.urlsafe_b64encode(public_raw).decode().rstrip("="),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Web Push VAPID keys.")
    parser.add_argument("--subject", default="mailto:caregiver@example.com")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    keys = generate(args.subject)
    if not args.quiet:
        print("# Paste these into backend/.env (git ignores it). Keep the private key secret.")
    for name, value in keys.items():
        print(f"{name}={value}")
    if not args.quiet:
        print("# Then restart the backend; GET /config must show the public key")
        print("# only when SAHAARA_PUSH_ENABLED=true.")
        print("# Rotating the keys invalidates every existing subscription: caregivers")
        print("# must press 'Enable alerts' again on each phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
