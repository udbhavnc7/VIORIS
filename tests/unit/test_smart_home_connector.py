"""
Unit tests for the Phase 6 Smart-home connector.

Covers: hub pair/unpair lifecycle, Observe-tier device status reads,
Execute-tier device control with approval-ledger gating + idempotency replay,
and the hard safety rule that locks/alarms/security/doors/gates are NEVER
controllable (no approval can override that).
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    ConnectorError,
    ExpiredSessionError,
    PermissionNotApprovedError,
    RateLimitError,
)
from integrations.mock_providers.smart_home_mock import MockSmartHomeTransport
from integrations.smart_home.connector import (
    ControlResult,
    DeviceStatusList,
    SmartHomeConnector,
)
from integrations.token_vault import TokenVault
from packages.shared.permission_engine import PermissionEngine
from packages.shared.schemas import RiskTier, ToolRegistration


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "sh.db", key=Fernet.generate_key())


@pytest.fixture()
def transport() -> MockSmartHomeTransport:
    return MockSmartHomeTransport()


@pytest.fixture()
def sc(vault: TokenVault, transport: MockSmartHomeTransport) -> SmartHomeConnector:
    return SmartHomeConnector(vault, transport=transport)


@pytest.fixture(autouse=True)
def _reset_registry():
    PermissionEngine.reset()
    yield
    PermissionEngine.reset()


def _register() -> None:
    PermissionEngine.register(
        ToolRegistration(
            tool_name="smart_home.device_status",
            tier=RiskTier.OBSERVE,
            confirmation_required=False,
            description="status",
        )
    )
    PermissionEngine.register(
        ToolRegistration(
            tool_name="smart_home.control",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="control",
            diff_card_fields=["device_id", "device_name", "category", "action", "value", "channel"],
        )
    )


def _paired(sc: SmartHomeConnector, transport: MockSmartHomeTransport, ref: str = "hub-h") -> str:
    transport.complete_pair(ref)
    sc.complete_link(ref)
    return ref


def test_scopes_and_tools_are_separated(sc: SmartHomeConnector) -> None:
    scopes = sc.declared_scopes()
    assert scopes.read == ["local-hub:read-status"]
    assert scopes.write == ["local-hub:control-devices"]
    assert set(sc.tools) == {"smart_home.device_status", "smart_home.control"}


def test_link_unlink_lifecycle(sc: SmartHomeConnector, transport: MockSmartHomeTransport) -> None:
    ref = _paired(sc, transport)
    assert sc._vault.require_entry("smart_home", ref) is not None
    sc.unlink_hub(ref)
    assert sc._vault.load("smart_home", ref) is None
    with pytest.raises(ConnectionMissingError):
        sc.unlink_hub(ref)


def test_device_status_lists_all(sc: SmartHomeConnector, transport: MockSmartHomeTransport) -> None:
    _register()
    _paired(sc, transport)
    result = sc.device_status("hub-h")
    assert isinstance(result, DeviceStatusList)
    assert result.total == 5
    categories = {d.category for d in result.devices}
    assert {"light", "climate", "camera", "energy", "security"} <= categories


def test_device_status_filters_by_category(sc: SmartHomeConnector, transport: MockSmartHomeTransport) -> None:
    _register()
    _paired(sc, transport)
    result = sc.device_status("hub-h", category="light")
    assert result.total == 1
    assert result.devices[0].device_id == "light-living"


def test_device_status_expired_session_marks_expired(
    sc: SmartHomeConnector, transport: MockSmartHomeTransport
) -> None:
    _register()
    _paired(sc, transport)
    transport.fail_next = transport.FAIL_EXPIRED
    with pytest.raises(ExpiredSessionError):
        sc.device_status("hub-h")
    assert sc._vault.load("smart_home", "hub-h")["expired"] is True


def test_device_status_rate_limit_propagates(
    sc: SmartHomeConnector, transport: MockSmartHomeTransport
) -> None:
    _register()
    _paired(sc, transport)
    transport.fail_next = transport.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        sc.device_status("hub-h")


def test_control_requires_approved_ledger(sc: SmartHomeConnector, transport: MockSmartHomeTransport) -> None:
    _register()
    _paired(sc, transport)
    with pytest.raises(PermissionNotApprovedError):
        sc.control_device(
            "hub-h",
            device_id="light-living",
            action="on",
            value="on",
            idempotency_key="ctl-1",
            approval_verifier=lambda: False,
        )
    assert transport.controls == []  # nothing applied without approval


def test_control_fires_with_approved_ledger(
    sc: SmartHomeConnector, transport: MockSmartHomeTransport
) -> None:
    _register()
    _paired(sc, transport)
    ctl = sc.control_device(
        "hub-h",
        device_id="light-living",
        action="on",
        value="on",
        idempotency_key="ctl-2",
        approval_verifier=lambda: True,
    )
    assert isinstance(ctl, ControlResult)
    assert ctl.replay is False
    assert ctl.result == "applied"
    assert transport.controls[0]["device_id"] == "light-living"


def test_control_idempotency_replays_prior_result(
    sc: SmartHomeConnector, transport: MockSmartHomeTransport
) -> None:
    _register()
    _paired(sc, transport)
    first = sc.control_device(
        "hub-h",
        device_id="light-living",
        action="on",
        value="on",
        idempotency_key="ctl-3",
        approval_verifier=lambda: True,
    )
    assert first.replay is False
    second = sc.control_device(
        "hub-h",
        device_id="light-living",
        action="on",
        value="on",
        idempotency_key="ctl-3",
        approval_verifier=lambda: True,
    )
    assert second.replay is True
    assert len(transport.controls) == 1  # duplicate retry never re-applies


def test_control_safety_critical_never_fires(
    sc: SmartHomeConnector, transport: MockSmartHomeTransport
) -> None:
    _register()
    _paired(sc, transport)
    # Even with an approved ledger, the security lock is refused by design.
    with pytest.raises(ConnectorError, match="safety-critical"):
        sc.control_device(
            "hub-h",
            device_id="lock-front",
            action="unlock",
            value="unlock",
            idempotency_key="ctl-lock",
            approval_verifier=lambda: True,
        )
    assert transport.controls == []  # the refusal is the point


def test_control_unknown_device_is_loud(sc: SmartHomeConnector, transport: MockSmartHomeTransport) -> None:
    _register()
    _paired(sc, transport)
    with pytest.raises(ConnectorError, match="not found"):
        sc.control_device(
            "hub-h",
            device_id="ghost-device",
            action="on",
            value="on",
            idempotency_key="ctl-5",
            approval_verifier=lambda: True,
        )


def test_control_unpaired_hub_is_loud(sc: SmartHomeConnector) -> None:
    _register()
    with pytest.raises(ConnectionMissingError):
        sc.control_device(
            "never-paired",
            device_id="light-living",
            action="on",
            value="on",
            idempotency_key="ctl-6",
            approval_verifier=lambda: True,
        )
