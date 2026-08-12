"""
Scripted mock Smart-home transport for tests (Phase 6, Smart-home).

Implements the SmartHomeTransport protocol with a canned local hub (lights,
camera, thermostat, energy sensor, and a safety-critical door lock) plus
injectable expired states, so connector tests never touch a real hub. Mirrors
the real transport's PROHIBITIONS: the door lock is listable but must never be
controlled — the connector refuses safety-critical categories before this mock
is ever asked to act.
"""

from __future__ import annotations

from integrations.base import ExpiredSessionError, RateLimitError


class MockSmartHomeTransport:
    """A local-hub-shaped mock with a canned device set + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self) -> None:
        self.name = "smart_home"
        self.calls: list[str] = []
        self.linked_hubs: set[str] = set()
        self.fail_next: str | None = None
        self.controls: list[dict] = []

    def link(self) -> dict:
        self.calls.append("link")
        return {"status": "awaiting_pairing", "handle": "hub_token"}

    def is_linked(self, hub_ref: str) -> bool:
        self.calls.append(f"is_linked:{hub_ref[:8]}")
        return hub_ref in self.linked_hubs

    def complete_pair(self, hub_ref: str) -> None:
        """Test helper: mark a hub as paired."""
        self.linked_hubs.add(hub_ref)

    def list_status(self, hub_ref: str, *, category: str | None = None) -> list[dict]:
        self.calls.append(f"list_status:{category or '*'}")
        if hub_ref not in self.linked_hubs:
            raise ExpiredSessionError("hub not linked (401)")
        self._maybe_fail()
        devices = _DEVICES if category is None else [d for d in _DEVICES if d["category"] == category]
        return [dict(d) for d in devices]

    def control(self, hub_ref: str, device_id: str, action: str, value: str) -> dict:
        self.calls.append(f"control:{device_id}:{action}")
        if hub_ref not in self.linked_hubs:
            raise ExpiredSessionError("hub not linked (401)")
        self._maybe_fail()
        self.controls.append({"device_id": device_id, "action": action, "value": value})
        return {"device_id": device_id, "result": "applied"}

    def unlink(self, hub_ref: str) -> None:
        self.calls.append("unlink")
        self.linked_hubs.discard(hub_ref)

    # ── failure injection ─────────────────────────────────────────────────
    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 hub invalid")


_DEVICES = [
    {
        "device_id": "light-living",
        "name": "Living Room Light",
        "category": "light",
        "state": "off",
        "detail": {"brightness": 0},
    },
    {
        "device_id": "ac-bedroom",
        "name": "Bedroom AC",
        "category": "climate",
        "state": "off",
        "detail": {"temperature_c": 21},
    },
    {
        "device_id": "cam-front",
        "name": "Front Door Camera",
        "category": "camera",
        "state": "armed",
        "detail": {"online": True},
    },
    {
        "device_id": "energy-mains",
        "name": "Mains Usage",
        "category": "energy",
        "state": "reading",
        "detail": {"watts": 412},
    },
    {
        "device_id": "lock-front",
        "name": "Front Door Lock",
        "category": "security",
        "state": "locked",
        "detail": {},
    },
]
