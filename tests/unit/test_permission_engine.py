import pytest

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
)
from packages.shared.schemas import RiskTier, ToolRegistration

pytestmark = pytest.mark.usefixtures("empty_registry")


@pytest.fixture(autouse=True)
def empty_registry():
    PermissionEngine.reset()
    yield
    PermissionEngine.reset()


class TestRegistration:
    def test_register_and_classify_observe_tool(self):
        PermissionEngine.register(
            ToolRegistration(
                tool_name="test.read",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="Read something",
            )
        )
        result = PermissionEngine.classify("test.read")
        assert result.tier == RiskTier.OBSERVE
        assert result.confirmation_required is False
        assert result.allowed is True

    def test_duplicate_registration_rejected(self):
        reg = ToolRegistration(
            tool_name="test.read",
            tier=RiskTier.OBSERVE,
            confirmation_required=False,
            description="Read",
        )
        PermissionEngine.register(reg)
        with pytest.raises(ValueError, match="already registered"):
            PermissionEngine.register(reg)

    def test_execute_tool_requires_diff_card_fields(self):
        with pytest.raises(ValueError, match="diff_card_fields"):
            PermissionEngine.register(
                ToolRegistration(
                    tool_name="test.send",
                    tier=RiskTier.EXECUTE,
                    confirmation_required=True,
                    description="Send",
                )
            )

    def test_critical_tool_requires_diff_card_fields(self):
        with pytest.raises(ValueError, match="diff_card_fields"):
            PermissionEngine.register(
                ToolRegistration(
                    tool_name="test.pay",
                    tier=RiskTier.CRITICAL,
                    confirmation_required=True,
                    description="Pay",
                )
            )


class TestClassification:
    def test_unregistered_tool_is_blocked(self):
        with pytest.raises(UnknownToolError, match="not registered"):
            PermissionEngine.classify("system.anything")

    def test_execute_tier_requires_confirmation(self):
        PermissionEngine.register(
            ToolRegistration(
                tool_name="test.send",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="Send",
                diff_card_fields=["recipient", "content"],
            )
        )
        result = PermissionEngine.classify("test.send")
        assert result.confirmation_required is True
        assert result.requires_second_factor is False

    def test_critical_tier_requires_second_factor_and_cooldown(self):
        PermissionEngine.register(
            ToolRegistration(
                tool_name="test.pay",
                tier=RiskTier.CRITICAL,
                confirmation_required=True,
                description="Pay",
                diff_card_fields=["recipient", "amount"],
            )
        )
        result = PermissionEngine.classify("test.pay")
        assert result.confirmation_required is True
        assert result.requires_second_factor is True
        assert result.cooldown_seconds > 0


class TestFreeze:
    def test_frozen_registry_rejects_new_registrations(self):
        PermissionEngine.register(
            ToolRegistration(
                tool_name="test.read",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="Read",
            )
        )
        PermissionEngine.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            PermissionEngine.register(
                ToolRegistration(
                    tool_name="test.late",
                    tier=RiskTier.OBSERVE,
                    confirmation_required=False,
                    description="Late",
                )
            )


class TestPhase1Tools:
    def test_phase1_tools_register_and_freeze(self):
        from packages.shared.permission_engine import register_phase1_tools

        register_phase1_tools()
        PermissionEngine.freeze()

        assert PermissionEngine.is_registered("system.get_time")
        assert PermissionEngine.is_registered("system.open_app")
        assert PermissionEngine.is_registered("system.set_reminder")
        assert PermissionEngine.is_registered("system.stop")

        for tool in ("system.get_time", "system.open_app", "system.stop"):
            assert PermissionEngine.classify(tool).confirmation_required is False

    def test_phase1_has_no_execute_or_critical_tools(self):
        from packages.shared.permission_engine import register_phase1_tools

        register_phase1_tools()
        for reg in PermissionEngine.list_tools():
            assert reg.tier not in (RiskTier.EXECUTE, RiskTier.CRITICAL)
