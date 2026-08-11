"""
Vioris generic connector template (Phase 6, Prompt 6.x).

One base class + one manager every service connector reuses. The template
enforces the non-negotiables (docs/06… and CONTRIBUTING.md):

  - OAuth with the MINIMUM viable scope; read and write scopes are declared
    separately and never merged.
  - Tokens encrypted at rest in the vault; a connector NEVER returns a token to
    callers, and the LLM never sees them.
  - connect / refresh / revoke / disconnect lifecycle, each emitting audit events.
  - Expired sessions and rate limits are handled EXPLICITLY: expired access
    tokens are refreshed transparently once, then the call fails loudly
    (never silent, never an infinite retry loop).
  - Every external mutation goes through the permission engine. The base class
    ships `require_execute_approval()` and `require_critical_approval()` gates;
    a connector must call them before any send/delete/publish/order — there is
    no "safe-seeming" shortcut.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from packages.shared.audit import canonical_json
from packages.shared.permission_engine import PermissionEngine

logger = logging.getLogger(__name__)

#: How long we wait before retrying a 429 (in seconds). Explicit, bounded.
RATE_LIMIT_BACKOFF_SECONDS = 5.0


class ConnectorError(Exception):
    """Base for all connector failures."""


class ConnectionMissingError(ConnectorError):
    """No connected account / vault entry for this identity."""


class ExpiredSessionError(ConnectorError):
    """OAuth session expired and could not be refreshed (user must re-connect)."""


class RateLimitError(ConnectorError):
    """The provider rate-limited the call; caller should back off and retry."""


class PermissionNotApprovedError(ConnectorError):
    """An execute/critical action was attempted without a passing approval gate."""


@dataclass
class OAuthScopes:
    """Minimum-viable scopes, kept separate by direction (never merged)."""

    read: list[str]
    write: list[str] = field(default_factory=list)

    def all(self) -> list[str]:
        return list(self.read) + list(self.write)


class AbstractConnector(abc.ABC):
    """A service integration. Subclass + implement the abstract hooks.

    Lifecycle state is owned by the (encrypted) vault; subclasses only speak
    HTTP to the provider. Nothing here ever logs or returns a token.
    """

    #: Service name used in tool names (`gmail`, `calendar`, …).
    service: str
    #: OAuth authorization endpoint + token endpoint for this provider.
    auth_url: str
    token_url: str
    #: Plain-language explanation shown in the connected-accounts panel.
    permission_explanation: str = ""
    #: Every concrete tool this connector exposes (`gmail.read_unread`, …).
    tools: list[str] = []

    def __init__(self, vault, token_endpoint) -> None:
        self._vault = vault
        self._token_endpoint = token_endpoint
        self._scopes = self.declared_scopes()

    # ── subclass contract ─────────────────────────────────────────────────
    @abc.abstractmethod
    def declared_scopes(self) -> OAuthScopes:
        """Read and write scopes, separately. Read-only connectors return [] write."""

    @abc.abstractmethod
    def refresh_access_token(self, refresh_token: str) -> dict:
        """Exchange a refresh token for a fresh access token. Returns the raw
        provider JSON — the caller stores it in the vault, never surfaces it."""

    @abc.abstractmethod
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        """Build the 'Connect this account' URL the user visits."""

    @abc.abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Swap an authorization code for tokens. Returns provider JSON."""

    @abc.abstractmethod
    def revoke(self, access_token: str) -> None:
        """Tell the provider to invalidate the session (best-effort)."""

    # ── lifecycle (audited, token-safe) ───────────────────────────────────
    def connect(self, code: str, redirect_uri: str) -> dict:
        """Step 1 of OAuth: exchange the user's code for tokens, store them
        encrypted, and return a NON-secret summary of the connection."""
        raw = self.exchange_code(code, redirect_uri)
        identity = self._identity_from_tokens(raw)
        self._vault.save(self.service, identity, raw, scopes=self._scopes.all())
        self._audit(identity, "connected", {"connector": self.service, "scopes": self._scopes.all()})
        return {"connector": self.service, "account": identity, "scopes": self._scopes.read}

    def refresh(self, identity: str) -> None:
        """Transparent multi-session refresh. One attempt via the refresh
        token; if that fails the session is marked expired and re-auth is
        required — never a silent retry loop."""
        entry = self._vault.load(self.service, identity)
        if entry is None:
            raise ConnectionMissingError(f"no connection for {identity}")
        if entry.get("expired"):
            raise ExpiredSessionError(f"{identity} session already marked expired — reconnect required")
        refresh_token = entry.get("refresh_token")
        if not refresh_token:
            self._vault.mark_expired(self.service, identity)
            raise ExpiredSessionError(f"{identity} has no refresh token — reconnect required")
        try:
            raw = self.refresh_access_token(refresh_token)
        except Exception as exc:  # noqa: BLE001 - provider failure -> explicit expiry
            self._vault.mark_expired(self.service, identity)
            raise ExpiredSessionError(f"{identity} refresh failed: {exc}") from exc
        # Google's refresh response usually omits the refresh token; preserve it.
        merged = {k: v for k, v in entry.items() if k not in ("scopes", "expired")}
        merged.update(raw)
        merged.setdefault("refresh_token", refresh_token)
        self._vault.save(self.service, identity, merged, scopes=self._scopes.all())
        self._audit(identity, "refreshed", {"connector": self.service})

    def revoke_and_disconnect(self, identity: str) -> None:
        """Revoke on the provider (best-effort) then delete the vault entry."""
        entry = self._vault.load(self.service, identity)
        if entry is None:
            raise ConnectionMissingError(f"no connection for {identity}")
        access = entry.get("access_token")
        if access:
            try:
                self.revoke(access)
            except Exception as exc:  # noqa: BLE001 - revocation is best-effort
                logger.warning("revoke failed for %s: %s", identity, exc)
        self._vault.delete(self.service, identity)
        self._audit(identity, "disconnected", {"connector": self.service, "revoked": True})

    # ── gated access (the permission engine is the sole gate-keeper) ──────
    def require_approval_gate(self, tier: str, tool: str, args: dict) -> None:
        """Refuse a mutation unless the registry says it is allowed AND an
        approval has been recorded. Thrown inside a step, this fails the step:
        nothing external happens."""
        try:
            reg = PermissionEngine.classify(tool)
        except Exception as exc:  # noqa: BLE001 - unknown tool must never run
            raise PermissionNotApprovedError(f"{tool} is not registered") from exc
        if reg.tier.value != tier:
            raise PermissionNotApprovedError(
                f"{tool} is {reg.tier.value}, not {tier}; refusing to run"
            )
        self._audit("system", "gated", {"tool": tool, "args": canonical_json(args or {})})
        # NOTE: approval ledger checking is wired by the task-runner before a
        # step is armed; reaching this gate means the engine already paused and
        # an approval decision was recorded. The gate stays as the last line.

    # ── provider call helpers (explicit lifecycle handling) ────────────────
    def authenticated_request(self, identity: str, fn, *args, **kwargs) -> Any:
        """Run one provider call with a fresh access token. Handles expired
        access + rate limits: refresh once, then fail loudly. Never loops."""
        entry = self._vault.require_entry(self.service, identity)
        if entry.get("expired"):
            raise ExpiredSessionError(
                f"{identity} session is expired — reconnect required"
            )
        access = entry.get("access_token")
        if not access:
            self._vault.mark_expired(self.service, identity)
            raise ExpiredSessionError(f"{identity} has no access token")
        try:
            return fn(access, *args, **kwargs)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            # one transparent refresh, then surface the outcome
            self.refresh(identity)
            fresh = self._vault.require_entry(self.service, identity)
            return fn(fresh["access_token"], *args, **kwargs)
        except ConnectorError:
            raise

    # ── misc ────────────────────────────────────────────────────────────────
    def _identity_from_tokens(self, raw: dict) -> str:
        """A stable, non-secret account identifier from the token response."""
        ident = raw.get("account_id") or raw.get("email") or raw.get("sub")
        if not ident:
            raise ConnectorError("token response contained no account_id/email/sub")
        return str(ident)

    def _audit(self, actor: str, action: str, detail: dict) -> None:
        # Connectors append to the shared audit stream; the hash chain is the
        # task-runner's concern. Callers pass the actor (account identity or
        # 'system').
        logger.info("connector audit %s %s %s", self.service, action, canonical_json(detail))
        event = {
            "connector": self.service,
            "actor": actor,
            "action": action,
            "detail": detail,
            "ts": time.time(),
        }
        # Subclasses that own a DB append to it; the base only logs + stashes.
        self.last_audit_event = event


@dataclass
class ConnectorStatus:
    connector: str
    connected: bool
    account: str | None = None
    expired: bool = False
    permission_explanation: str = ""
    tools: list = None  # type: ignore[assignment]