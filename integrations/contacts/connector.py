"""
Contacts connector — READ-ONLY (Phase 6, order: … → Contacts → …).

Minimum-viable scope: `contacts.readonly`. It lists/search recent contacts
and produces a digest. There are NO write scopes; creating, editing, or
deleting contacts is intentionally unregistered, so any such path is blocked
by the permission engine.

OAuth + token handling mirror the Gmail connector exactly: encrypted vault,
transparent single refresh, loud rate-limit / expired-session failures, and an
injected transport so tests use a scripted mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
    RateLimitError,
)

_DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CONTACTS_API = "https://people.googleapis.com/v1"

# Minimum-viable read scope.
SCOPE_READONLY = "https://www.googleapis.com/auth/contacts.readonly"

PERMISSION_EXPLANATION = (
    "Read only your contacts: names, email addresses, and phone numbers. "
    "It cannot create, edit, or delete any contact."
)


class ContactsTransport(Protocol):
    """The small HTTP surface a contacts connector needs (mockable)."""

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    def refresh_access_token(self, refresh_token: str) -> dict: ...

    def revoke(self, access_token: str) -> None: ...

    def list_contacts(
        self, access_token: str, *, max_results: int = 30, query: str = ""
    ) -> dict:
        """GET people.connections.list → {'connections': [...], 'nextPageToken': ...}."""


@dataclass
class Contact:
    person_id: str
    name: str
    emails: list[str]
    phones: list[str]
    summary: str


@dataclass
class ContactsDigest:
    account: str
    total: int
    truncated: bool
    contacts: list[Contact]


def _name(person: dict) -> str:
    names = person.get("names") or []
    if names:
        return names[0].get("displayName") or names[0].get("givenName") or "(unnamed)"
    return "(unnamed)"


def _emails(person: dict) -> list[str]:
    return [e.get("value", "") for e in person.get("emailAddresses", []) or [] if e.get("value")]


def _phones(person: dict) -> list[str]:
    return [p.get("value", "") for p in person.get("phoneNumbers", []) or [] if p.get("value")]


def _summary(person: dict) -> str:
    name = _name(person)
    emails = _emails(person)
    phones = _phones(person)
    bits = emails[:1] + phones[:1]
    return f"{name} — {' / '.join(bits) if bits else 'no contact details'}"


class HttpxContactsTransport:
    """Production transport against Google's OAuth + People REST APIs."""

    def __init__(self, client_id: str, client_secret: str,
                 auth_url: str = _DEFAULT_AUTH_URL,
                 token_url: str = _DEFAULT_TOKEN_URL) -> None:
        import httpx  # lazy: the connector imports without dependencies
        self._httpx = httpx
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = auth_url
        self.token_url = token_url

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        return (
            f"{self.auth_url}?client_id={self.client_id}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope={scope}&access_type=offline&prompt=consent&state={state}"
        )

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        resp = self._httpx.post(
            self.token_url,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("exchange_code: 429")
        resp.raise_for_status()
        return resp.json()

    def refresh_access_token(self, refresh_token: str) -> dict:
        resp = self._httpx.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("refresh_access_token: 429")
        if resp.status_code == 400:
            raise ConnectorError("expired: refresh token rejected")
        resp.raise_for_status()
        return resp.json()

    def revoke(self, access_token: str) -> None:
        self._httpx.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": access_token},
            timeout=20,
        )

    def list_contacts(self, access_token: str, *, max_results: int = 30, query: str = "") -> dict:
        params = {
            "pageSize": str(max_results),
            "personFields": "names,emailAddresses,phoneNumbers",
        }
        resp = self._httpx.get(
            f"{_CONTACTS_API}/people/me/connections",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("list_contacts: 429")
        if resp.status_code in (401, 403):
            raise ConnectorError("expired: access token rejected")
        resp.raise_for_status()
        return resp.json()


class ContactsConnector(AbstractConnector):
    """Read-only contacts integration. Tools: contacts.search."""

    service = "contacts"
    auth_url = _DEFAULT_AUTH_URL
    token_url = _DEFAULT_TOKEN_URL
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["contacts.search"]

    def __init__(self, vault, transport: ContactsTransport | None = None,
                 token_endpoint: str | None = None, **_ignored) -> None:
        super().__init__(vault, token_endpoint or self.token_url)
        self.transport = transport or HttpxContactsTransport("", "")

    def declared_scopes(self) -> OAuthScopes:
        # READ-ONLY: write scopes intentionally empty. Never merge.
        return OAuthScopes(read=[SCOPE_READONLY], write=[])

    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        return self.transport.authorize_uri(state, redirect_uri, " ".join(self._scopes.read))

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        return self.transport.exchange_code(code, redirect_uri)

    def refresh_access_token(self, refresh_token: str) -> dict:
        return self.transport.refresh_access_token(refresh_token)

    def revoke(self, access_token: str) -> None:
        self.transport.revoke(access_token)

    def search_contacts(self, identity: str, query: str = "", max_results: int = 30) -> ContactsDigest:
        """List/search contacts. Observe-tier. Query is a local post-filter; the
        transport always returns the connection list (read-only)."""
        entry = self._vault.require_entry(self.service, identity)
        listing = self.authenticated_request(
            identity,
            lambda at: self.transport.list_contacts(at, max_results=max_results),
        )
        raw = listing.get("connections", [])
        truncated = listing.get("nextPageToken") is not None
        q = (query or "").strip().lower()
        contacts = []
        for person in raw:
            c = Contact(
                person_id=person.get("resourceName", ""),
                name=_name(person),
                emails=_emails(person),
                phones=_phones(person),
                summary=_summary(person),
            )
            if q and q not in (c.name + " ".join(c.emails) + " ".join(c.phones)).lower():
                continue
            contacts.append(c)
        return ContactsDigest(
            account=self._identity_from_tokens(entry),
            total=len(contacts),
            truncated=truncated,
            contacts=contacts,
        )