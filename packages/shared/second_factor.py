"""Second-factor proof for Critical-tier approvals.

Critical actions need more than a click. The gateway verifies a locally
configured PIN, or a fresh voice sample against the enrolled voiceprint inside
a voiceprint-verified call, then mints a short-lived HMAC proof bound to one
approval id. The task-runner verifies that proof before arming a Critical step,
so calling the task-runner API directly cannot bypass the second factor.

The proof records which factor was used (``pin`` or ``voiceprint``) and the
task-runner writes that into the audit chain. Both factors are local-only; the
HMAC key is a local secret file with owner-only permissions and is never sent
to the browser, logged, or included in audit detail.
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

PIN_FACTOR = "pin"
VOICEPRINT_FACTOR = "voiceprint"
_ALLOWED_FACTORS = (PIN_FACTOR, VOICEPRINT_FACTOR)


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


def _payload(approval_id: str, exp: int, factor: str) -> bytes:
    """Sign over the factor too, so a PIN proof can never be relabelled."""
    if factor == PIN_FACTOR:
        return f"{approval_id}:{exp}".encode()
    return f"{approval_id}:{exp}:{factor}".encode()


def mint(
    approval_id: str,
    key: bytes,
    now: float | None = None,
    factor: str = PIN_FACTOR,
) -> str:
    """Mint an approval-scoped, short-lived proof: <exp>.<sig>.

    Voiceprint proofs carry their factor in the token (``<exp>.voiceprint.<sig>``)
    so the verifier — and the audit chain — can tell how consent was obtained.
    """
    if factor not in _ALLOWED_FACTORS:
        raise ValueError(f"unknown second-factor type: {factor}")
    exp = int((now if now is not None else time.time()) + PROOF_TTL_SECONDS)
    sig = _signature(_payload(approval_id, exp, factor), key)
    if factor == PIN_FACTOR:
        return f"{exp}.{sig}"
    return f"{exp}.{factor}.{sig}"


def _parse(proof: str | None) -> tuple[int, str, str] | None:
    if not proof:
        return None
    try:
        parts = proof.split(".")
        if len(parts) == 2:
            exp_raw, sig = parts
            factor = PIN_FACTOR
        elif len(parts) == 3 and parts[1] == VOICEPRINT_FACTOR:
            exp_raw, _, sig = parts
            factor = VOICEPRINT_FACTOR
        else:
            return None
        return int(exp_raw), factor, sig
    except (ValueError, AttributeError):
        return None


def verify_with_factor(
    proof: str | None,
    approval_id: str,
    key: bytes,
    now: float | None = None,
    allowed_factors: tuple[str, ...] = _ALLOWED_FACTORS,
) -> tuple[bool, str, str]:
    """Verify a proof for exactly this approval. Fails closed on any doubt.

    Returns ``(ok, note, factor)``. ``factor`` is the factor claimed by the
    token even when verification fails, so refused attempts stay auditable.
    """
    parsed = _parse(proof)
    if parsed is None:
        factor = VOICEPRINT_FACTOR if proof and ".voiceprint." in proof else PIN_FACTOR
        if not proof:
            return False, "second factor proof is missing", factor
        return False, "second factor proof is malformed", factor
    exp, factor, sig = parsed
    if factor not in allowed_factors:
        return False, f"second factor '{factor}' is not allowed here", factor
    current = now if now is not None else time.time()
    if exp < current:
        return False, "second factor proof expired", factor
    if not hmac.compare_digest(sig, _signature(_payload(approval_id, exp, factor), key)):
        return False, "second factor proof is invalid", factor
    return True, "second factor verified", factor


def proof_factor(proof: str | None) -> str:
    """Report the factor claimed by a token (even a malformed one), for audit.

    This does not verify anything — call it only after :func:`verify` succeeds.
    """
    parsed = _parse(proof)
    if parsed is not None:
        return parsed[1]
    return VOICEPRINT_FACTOR if proof and ".voiceprint." in proof else PIN_FACTOR


def verify(
    proof: str | None,
    approval_id: str,
    key: bytes,
    now: float | None = None,
    allowed_factors: tuple[str, ...] = _ALLOWED_FACTORS,
) -> tuple[bool, str]:
    """Backward-compatible wrapper around :func:`verify_with_factor`."""
    ok, note, _ = verify_with_factor(proof, approval_id, key, now, allowed_factors)
    return ok, note


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
