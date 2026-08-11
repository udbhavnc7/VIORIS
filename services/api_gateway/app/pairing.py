"""
Device pairing store (Phase 5, Prompt 5.1).

Pairing flow:
  1. `issue_pairing(name)` emits a short-lived, QR-encoded pairing secret plus
     the device id it belongs to. The phone scans the token — no magic URLs,
     no inbound ports.
  2. `exchange(device_id, token)` swaps a scanned, unexpired pairing token for
     a device-scoped, expiring JWT (`sub` = device_id).
  3. Every later WebSocket connection is verified with `verify(jwt)`, which
     rejects an invalid/expired token or a revoked device.

Revocation is permanent: `revoke(device_id)` flags the device so even an
un-expired JWT stops working immediately.

Signing key: env `VIORIS_PAIRING_SECRET`, else a random secret generated at
startup (tokens will not survive a gateway restart unless the operator sets
the env var).
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

logger = logging.getLogger(__name__)

PAIRING_TTL_SECONDS = 600  # pairing tokens valid 10 minutes
DEFAULT_TOKEN_TTL_SECONDS = 86400 * 7  # device JWT valid 7 days
_ALGO = "HS256"


class PairingError(Exception):
    """Root of all pairing failures (rejected at the API boundary)."""


class InvalidPairingTokenError(PairingError):
    pass


class PairingExpiredError(PairingError):
    pass


class DeviceRevokedError(PairingError):
    pass


class InvalidDeviceTokenError(PairingError):
    pass


@dataclass
class Device:
    device_id: str
    name: str
    created_at: str
    revoked: bool = False

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "created_at": self.created_at,
            "revoked": self.revoked,
        }


@dataclass
class PairingProof:
    device_id: str
    token: str  # QR-encoded pairing secret (single use, TTL-scoped)
    expires_at: float


@dataclass
class ExchangedToken:
    device_id: str
    jwt: str
    expires_at: float


class PairingStore:
    """In-memory pairing ledger: pending tokens + known devices."""

    def __init__(self, secret: str | None = None, token_ttl: int = DEFAULT_TOKEN_TTL_SECONDS) -> None:
        self._secret = secret or os.getenv("VIORIS_PAIRING_SECRET") or secrets.token_hex(32)
        self.token_ttl = token_ttl
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}  # pairing -> (device_id, expires)
        self._devices: dict[str, Device] = {}

    # ── step 1: issue pairing token ───────────────────────────────────────
    def issue_pairing(self, name: str) -> PairingProof:
        device_id = f"dev_{uuid.uuid4().hex[:10]}"
        # one-time token; opaque aside from its own single-use nature
        token = f"vioris-pair-{secrets.token_urlsafe(24)}"
        expires = time.time() + PAIRING_TTL_SECONDS
        with self._lock:
            if device_id in self._devices:
                raise PairingError("device id collision")  # pragma: no cover - 128-bit ids
            self._pending[token] = (device_id, expires)
            self._devices[device_id] = Device(
                device_id=device_id, name=name, created_at=_now().isoformat()
            )
        return PairingProof(device_id=device_id, token=token, expires_at=expires)

    # ── step 2: exchange scanned token for a device JWT ─────────────────
    def exchange(self, device_id: str, token: str) -> ExchangedToken:
        with self._lock:
            entry = self._pending.pop(token, None)
            already_used = entry is None and self._devices.get(device_id) is not None
        device = self._devices.get(device_id)
        if device is None:
            raise InvalidPairingTokenError(f"unknown device '{device_id}'")
        if device.revoked:
            raise DeviceRevokedError(f"device '{device_id}' is revoked")
        if already_used:
            raise InvalidPairingTokenError("pairing token already used")
        if entry is None:
            raise InvalidPairingTokenError("pairing token is unknown")
        entry_device, expires_at = entry
        if entry_device != device_id:
            raise InvalidPairingTokenError("pairing token was issued to a different device")
        if expires_at < time.time():
            raise PairingExpiredError("pairing token expired")
        return self._issue_jwt(device)

    def _issue_jwt(self, device: Device) -> ExchangedToken:
        now = _now()
        exp = int((now + timedelta(seconds=self.token_ttl)).timestamp())
        payload = {
            "sub": device.device_id,
            "type": "device",
            "iat": int(now.timestamp()),
            "exp": exp,
        }
        token = jwt.encode(payload, self._secret, algorithm=_ALGO)
        return ExchangedToken(device_id=device.device_id, jwt=token, expires_at=exp)

    # ── later: verify a device JWT on every WebSocket connect ────────────
    def verify(self, jwt_token: str) -> Device:
        """Return the device iff the JWT is valid and the device is not revoked."""
        if not jwt_token:
            raise InvalidDeviceTokenError("missing token")
        try:
            payload = jwt.decode(jwt_token, self._secret, algorithms=[_ALGO])
        except jwt.ExpiredSignatureError as exc:
            raise InvalidDeviceTokenError("token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidDeviceTokenError("token invalid") from exc
        device_id = payload.get("sub")
        if not device_id:
            raise InvalidDeviceTokenError("token has no subject")
        with self._lock:
            device = self._devices.get(device_id)
        if device is None:
            raise InvalidDeviceTokenError("token issued to an unknown device")
        if device.revoked:
            raise DeviceRevokedError(f"device '{device_id}' is revoked")
        return device

    # ── admin ────────────────────────────────────────────────────────────
    def list_devices(self) -> list[Device]:
        with self._lock:
            return list(self._devices.values())

    def revoke(self, device_id: str) -> bool:
        """Permanently revoke a device. Returns False if it was unknown."""
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return False
            device.revoked = True
            return True


def _now() -> datetime:
    return datetime.now(timezone.utc)