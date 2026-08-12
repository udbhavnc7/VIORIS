"""
Smart-home connector — Phase 6, order: … → Smart-home.

Controls a LOCAL IoT hub API (per docs/02: "Local IoT hub API"). Two tools,
kept strictly separate:

  - `smart_home.device_status` (OBSERVE): read device/camera/sensor/energy
    status. Nothing changes; no approval.
  - `smart_home.control` (EXECUTE): change a device's state (lights, fans, AC,
    music, TV). ONLY fires with an approved record in the task-runner ledger
    for that step's idempotency_key, and the diff card shows the device, the
    exact action, and the target value — the full "what will happen" payload.

Hard limits (feature catalog §I): this connector NEVER touches locks, alarms,
or security systems — those tools are intentionally unregistered and blocked.
Nothing controls a device without the permission engine; rejection or a missing
approval never fires a command. Failures are explicit and loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    PermissionNotApprovedError,
    RateLimitError,
)

PERMISSION_EXPLANATION = (
    "Reads status from your local smart-home hub (lights, cameras, sensors, "
    "energy). Can also change device state — lights, fans, AC, music, TV — "
    "but only after you approve a diff card showing the device and the exact "
    "action. It can NEVER touch locks, alarms, or security systems."
)

#: Browser-session-style capability surface (no OAuth; local hub).
_READ_SCOPE = "local-hub:read-status"
_WRITE_SCOPE = "local-hub:control-devices"

#: Safety-critical categories this connector refuses on sight.
_FORBIDDEN_CATEGORIES = ("lock", "alarm", "security", "door", "gate")


@dataclass
class DeviceStatus:
    device_id: str
    name: str
    category: str
    state: str
    detail: dict = field(default_factory=dict)


@dataclass
class DeviceStatusList:
    total: int
    devices: list[DeviceStatus] = field(default_factory=list)


@dataclass
class ControlResult:
    device_id: str
    action: str
    value: str
    result: str
    idempotency_key: str
    replay: bool = False


class SmartHomeTransport(Protocol):
    """The local-hub surface a smart-home connector needs."""

    def list_status(self, hub_ref: str, *, category: str | None = None) -> list[dict]: ...

    def control(self, hub_ref: str, device_id: str, action: str, value: str) -> dict:
        """Send a control command to `device_id`. Returns the hub result."""

    def is_linked(self, hub_ref: str) -> bool: ...

    def link(self) -> dict:
        """Start linking a local hub session (token/pairing handle)."""

    def unlink(self, hub_ref: str) -> None: ...


class SmartHomeConnector(AbstractConnector):
    """Local smart-home hub connector. Tools: smart_home.device_status
    (Observe), smart_home.control (Execute, approval-gated)."""

    service = "smart_home"
    auth_url = ""  # local hub: no OAuth endpoints
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["smart_home.device_status", "smart_home.control"]

    def __init__(self, vault, transport: SmartHomeTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport

    def declared_scopes(self) -> OAuthScopes:
        return OAuthScopes(read=[_READ_SCOPE], write=[_WRITE_SCOPE])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("smart_home uses a local hub pairing, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("smart_home uses a local hub pairing, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("smart_home uses local hub pairing, not OAuth refresh")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("smart_home unlinks the local hub, not OAuth revoke")

    # ── session lifecycle (vault-backed) ───────────────────────────────────
    def link_hub(self) -> dict:
        handle = self.transport.link()
        return handle

    def complete_link(self, hub_ref: str) -> dict:
        if not self.transport.is_linked(hub_ref):
            raise ConnectorError("smart-home hub not paired on the provider side")
        self._vault.save(self.service, hub_ref, {"hub_ref": hub_ref},
                         scopes=[_READ_SCOPE, _WRITE_SCOPE])
        self._audit(hub_ref, "linked", {"connector": self.service})
        return {"connector": self.service, "account": hub_ref,
                "scopes": self._scopes.read}

    def unlink_hub(self, hub_ref: str) -> None:
        self._vault.require_entry(self.service, hub_ref)
        self.transport.unlink(hub_ref)
        self._vault.delete(self.service, hub_ref)
        self._audit(hub_ref, "unlinked", {"connector": self.service})

    # ── status path (observe-tier, transparent) ────────────────────────────
    def device_status(self, hub_ref: str, *, category: str | None = None) -> DeviceStatusList:
        entry = self._vault.require_entry(self.service, hub_ref)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{hub_ref} session expired — re-pair required")
        try:
            raws = self.transport.list_status(hub_ref, category=category)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            self._vault.mark_expired(self.service, hub_ref)
            raise
        except ConnectorError:
            raise
        devices = [
            DeviceStatus(
                device_id=r["device_id"],
                name=r["name"],
                category=r["category"],
                state=r["state"],
                detail=r.get("detail", {}),
            )
            for r in raws
        ]
        return DeviceStatusList(total=len(devices), devices=devices)

    # ── control path (execute-tier, approval-gated, idempotent) ────────────
    def control_device(
        self,
        hub_ref: str,
        *,
        device_id: str,
        action: str,
        value: str,
        idempotency_key: str,
        approval_verifier=None,
    ) -> ControlResult:
        """Change a device's state, ONLY with an approved ledger record.

        Safety: devices in `_FORBIDDEN_CATEGORIES` (locks, alarms, security,
        doors, gates) are refused here, always — no approval can override it.
        """
        if not device_id or not device_id.strip():
            raise ConnectorError("refusing to control with no device id")
        if not action or not action.strip():
            raise ConnectorError("refusing to control with no action")

        entry = self._vault.require_entry(self.service, hub_ref)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{hub_ref} session expired — re-pair required")

        # Safety-critical categories are uncontrolable by design.
        device = self._find_device(hub_ref, device_id)
        category = (device.get("category") or "").lower()
        if any(fb in category for fb in _FORBIDDEN_CATEGORIES):
            raise ConnectorError(
                f"device '{device_id}' is in a safety-critical category "
                f"({category}); this connector never controls it"
            )

        # Idempotency: a replayed key returns the prior result, never re-sends.
        prior = self._vault.load(f"{self.service}_controls", idempotency_key)
        if prior and prior.get("status") == "applied":
            return ControlResult(
                device_id=prior["device_id"],
                action=prior["action"],
                value=prior["value"],
                result=prior["result"],
                idempotency_key=idempotency_key,
                replay=True,
            )

        # The diff card must show the device, the exact action, and the target
        # value — the full "what will happen" payload.
        self.require_approval_gate(
            "execute",
            "smart_home.control",
            {
                "device_id": device_id,
                "device_name": device.get("name", device_id),
                "category": category,
                "action": action,
                "value": value,
                "channel": "local-hub",
            },
        )
        if approval_verifier is not None and not approval_verifier():
            raise PermissionNotApprovedError(
                "no approved approval exists for this control (task-runner ledger)"
            )

        try:
            raw = self.transport.control(hub_ref, device_id, action, value)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            self._vault.mark_expired(self.service, hub_ref)
            raise
        except ConnectorError:
            raise

        result = raw.get("result", "ok")
        self._vault.save(
            f"{self.service}_controls",
            idempotency_key,
            {
                "device_id": device_id,
                "action": action,
                "value": value,
                "result": result,
                "status": "applied",
            },
        )
        self._audit(
            hub_ref,
            "controlled_device",
            {
                "device_id": device_id,
                "category": category,
                "action": action,
                "value": value,
                "result": result,
                "idempotency_key": idempotency_key,
            },
        )
        return ControlResult(
            device_id=device_id,
            action=action,
            value=value,
            result=result,
            idempotency_key=idempotency_key,
        )

    def _find_device(self, hub_ref: str, device_id: str) -> dict:
        try:
            raws = self.transport.list_status(hub_ref, category=None)
        except ConnectorError:
            raise
        for r in raws:
            if r.get("device_id") == device_id:
                return r
        raise ConnectorError(f"device '{device_id}' not found on the hub")
