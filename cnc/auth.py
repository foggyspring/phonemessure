"""Authentication: password hashing + stateless signed tokens (stdlib only).

No external deps (no bcrypt/jwt): passwords use PBKDF2-HMAC-SHA256 with a random
salt; sessions are HMAC-signed tokens carrying {username, role, exp}, verified in
constant time. The signing secret comes from $CNC_SECRET, else a generated value
persisted in the DB so tokens survive restarts.

Used to gate the admin/price endpoints — previously anyone who could reach the
API could rewrite material prices and machine rates.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_PBKDF2_ITERS = 200_000
_ALGO = "pbkdf2_sha256"


# --------------------------------------------------------------- passwords --
def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"{_ALGO}${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ tokens --
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(secret: str, username: str, role: str, ttl_s: int = 8 * 3600) -> str:
    payload = {"u": username, "r": role, "exp": int(time.time()) + ttl_s}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(secret: str, token: str) -> dict | None:
    """Return the payload if the token is valid and unexpired, else None."""
    try:
        body, sig = token.split(".")
    except (ValueError, AttributeError):
        return None
    expected = _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def bearer_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None
