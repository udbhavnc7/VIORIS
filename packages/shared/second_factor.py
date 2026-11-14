"""Second-factor proof for Critical-tier approvals.

Critical actions need more than a click. The gateway verifies a locally
configured PIN, then mints a short-lived HMAC proof bound to one approval id.
The task-runner verifies that proof before arming a Critical step, so calling
the task-runner API directly cannot bypass the second factor.

The HMAC key is a local secret file with owner-only permissions. It is never
sent to the browser, logged, or included in audit detail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

DEFAULT_KEY_PATH = Path("vioris_data/second_factor.key")
PROOF_TTL_SECONDS = 120


def _key_path(path: str | Path | None = None) -> Path:
    return Path(path or os.getenv("VIORIS_SECOND_FACTOR_KEY_FILE") or DEFAULT_KEY_PATH)


def load_or_create_key(path: str | Path | None = None) -> bytes:
    """Read the local HMAC key, creating it with owner-only permissions once."""
    target = _key_path(path)
    if target.exists():
        return target.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    # O_EXCL: never clobber a key another process created concurrently.
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return target.read_bytes()
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _signature(payload: bytes, key: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def mint(approval_id: str, key: bytes, now: float | None = None) -> str:
    """Mint an approval-scoped, short-lived proof: <exp>.<sig>."""
    exp = int((now if now is not None else time.time()) + PROOF_TTL_SECONDS)
    payload = f"{approval_id}:{exp}".encode()
    return f"{exp}.{_signature(payload, key)}"


def verify(proof: str | None, approval_id: str, key: bytes, now: float | None = None) -> tuple[bool, str]:
    """Verify a proof for exactly this approval. Fails closed on any doubt."""
    if not proof:
        return False, "second factor proof is missing"
    try:
        exp_raw, sig = proof.split(".", 1)
        exp = int(exp_raw)
    except (ValueError, AttributeError):
        return False, "second factor proof is malformed"
    current = now if now is not None else time.time()
    if exp < current:
        return False, "second factor proof expired"
    payload = f"{approval_id}:{exp}".encode()
    if not hmac.compare_digest(sig, _signature(payload, key)):
        return False, "second factor proof is invalid"
    return True, "second factor verified"


def pin_matches(candidate: str | None) -> tuple[bool, str]:
    """Check the locally configured Critical PIN. Unset PIN = locked, never open."""
    configured = os.getenv("VIORIS_CRITICAL_PIN", "")
    if not configured:
        return False, (
            "Critical actions are locked: no second factor is configured. "
            "Set VIORIS_CRITICAL_PIN before starting Vioris to unlock them."
        )
    if not candidate:
        return False, "This is a Critical action: enter your local PIN to continue."
    if not hmac.compare_digest(
        base64.b64encode(hashlib.sha256(candidate.encode()).digest()),
        base64.b64encode(hashlib.sha256(configured.encode()).digest()),
    ):
        return False, "The Critical-action PIN did not match."
    return True, ""
