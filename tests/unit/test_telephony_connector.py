"""
Unit tests for the Phase 6 Telephony (calling) connector.

Covers: Prepare-tier draft (shown, never dialed), Execute-tier native-dialer
handoff with approval-ledger gating, idempotency replay, resolved recipient
identity on the diff card, and loud ambiguity/empty handling. Mirrors the v1
rule: no autonomous call is ever placed.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectorError,
    PermissionNotApprovedError,
)
from integrations.mock_providers.telephony_mock import MockTelephonyTransport
from integrations.telephony.connector import (
    CallBrief,
    CallHandoff,
    TelephonyConnector,
    _draft_script,
    _draft_talking_points,
)
from integrations.token_vault import TokenVault
from packages.shared.permission_engine import PermissionEngine
from packages.shared.schemas import RiskTier, ToolRegistration


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "tel.db", key=Fernet.generate_key())


@pytest.fixture()
def transport() -> MockTelephonyTransport:
    return MockTelephonyTransport()


@pytest.fixture()
def tc(vault: TokenVault, transport: MockTelephonyTransport) -> TelephonyConnector:
    return TelephonyConnector(vault, transport=transport)


@pytest.fixture(autouse=True)
def _reset_registry():
    PermissionEngine.reset()
    yield
    PermissionEngine.reset()


def _register() -> None:
    PermissionEngine.register(
        ToolRegistration(
            tool_name="telephony.prepare_call",
            tier=RiskTier.PREPARE,
            confirmation_required=False,
            description="draft",
        )
    )
    PermissionEngine.register(
        ToolRegistration(
            tool_name="telephony.start_call",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="dialer handoff",
            diff_card_fields=["recipient", "recipient_identity", "phone", "script", "channel"],
        )
    )


def test_scopes_and_tools_are_separated(tc: TelephonyConnector) -> None:
    scopes = tc.declared_scopes()
    assert scopes.read == ["telephony:read-contacts"]
    assert scopes.write == ["telephony:open-dialer"]
    assert set(tc.tools) == {"telephony.prepare_call", "telephony.start_call"}


def test_prepare_drafts_brief_never_dials(
    tc: TelephonyConnector, transport: MockTelephonyTransport
) -> None:
    _register()
    brief = tc.prepare_call("Mum", purpose="confirm dinner")
    assert isinstance(brief, CallBrief)
    assert brief.drafted is True
    assert brief.recipient_identity == "Mum <+447700900001>"
    assert brief.phone == "+447700900001"
    assert "confirm dinner" in brief.script
    assert transport.handoffs == []  # prepared, never dialed


def test_prepare_refuses_empty_recipient(tc: TelephonyConnector) -> None:
    _register()
    with pytest.raises(ConnectorError, match="no recipient"):
        tc.prepare_call("   ")


def test_prepare_ambiguous_recipient_is_loud(tc: TelephonyConnector) -> None:
    _register()
    with pytest.raises(ConnectorError, match="ambiguous"):
        tc.prepare_call("Alex")


def test_start_call_requires_approved_ledger(tc: TelephonyConnector, transport: MockTelephonyTransport) -> None:
    _register()
    with pytest.raises(PermissionNotApprovedError):
        tc.start_call("Mum", idempotency_key="call-1", approval_verifier=lambda: False)
    assert transport.handoffs == []  # nothing opened without approval


def test_start_call_fires_with_approved_ledger(
    tc: TelephonyConnector, transport: MockTelephonyTransport
) -> None:
    _register()
    handoff = tc.start_call("Mum", idempotency_key="call-2", approval_verifier=lambda: True)
    assert isinstance(handoff, CallHandoff)
    assert handoff.replay is False
    assert handoff.recipient_identity == "Mum <+447700900001>"
    assert handoff.phone == "+447700900001"
    assert handoff.channel == "native-dialer"
    assert transport.handoffs[0]["phone"] == "+447700900001"


def test_start_call_idempotency_replays_prior_handoff(
    tc: TelephonyConnector, transport: MockTelephonyTransport
) -> None:
    _register()
    first = tc.start_call("Mum", idempotency_key="call-3", approval_verifier=lambda: True)
    second = tc.start_call("Mum", idempotency_key="call-3", approval_verifier=lambda: True)
    assert second.replay is True
    assert second.handoff_id == first.handoff_id
    assert len(transport.handoffs) == 1  # duplicate retry never opens the dialer twice


def test_start_call_ambiguous_recipient_is_loud(tc: TelephonyConnector) -> None:
    _register()
    with pytest.raises(ConnectorError, match="ambiguous"):
        tc.start_call("Alex", idempotency_key="call-4", approval_verifier=lambda: True)


def test_start_call_refuses_empty_recipient(tc: TelephonyConnector) -> None:
    _register()
    with pytest.raises(ConnectorError, match="no recipient"):
        tc.start_call("", idempotency_key="call-5", approval_verifier=lambda: True)


def test_drafting_helpers() -> None:
    from integrations.telephony.connector import ContactInfo

    c = ContactInfo(name="Jane", phone="+447700900004")
    points = _draft_talking_points("confirm the booking", c)
    assert any("confirm the booking" in p for p in points)
    assert "Jane" in _draft_script("", c)
