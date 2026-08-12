"""
Task-runner tool executor (Phase 5, Prompt 5.3; Phase 6 connectors).

Replaces the Phase 2 stand-in with a REAL router: execute/critical steps that
the user approved are dispatched to the correct local agent daemon or connector
over HTTP, then verified. The LLM never runs tools — the engine calls
`execute(step, task)`, which routes by tool name to the service that owns it.

Routes:
  - computer.* and system.open_app      -> computer agent daemon (VIORUS_COMPUTER_URL)
  - smart_home.*                        -> smart-home connector (VIORUS_SMART_HOME_URL)
  - all other Phase 6 connector tools   -> generic connector router (see
                                          _CONNECTOR_ROUTES). Observe reads map
                                          to GET endpoints with no approval;
                                          Execute writes map to POST endpoints
                                          and forward the (task, step,
                                          idempotency_key) approval triple so
                                          the connector re-verifies the ledger
                                          record before anything fires.
  - anything else                       -> refused (never executed silently)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

COMPUTER_URL = os.getenv("VIORUS_COMPUTER_URL", "http://127.0.0.1:8430")
SMART_HOME_URL = os.getenv("VIORUS_SMART_HOME_URL", "http://127.0.0.1:8455")


# ─── Generic Phase 6 connector routing ────────────────────────────────────────


@dataclass(frozen=True)
class _Route:
    """Static route for one connector tool: which service, method, path, and
    which args are allowed through. Written once, never decided at runtime."""

    service: str
    method: str  # "GET" or "POST"
    path: str
    query_keys: tuple[str, ...] = ()
    body_keys: tuple[str, ...] = ()
    approval: bool = False  # forward the (task, step, idempotency_key) triple


_CONNECTOR_SERVICES: dict[str, tuple[str, str]] = {
    "gmail": ("VIORUS_GMAIL_URL", "http://127.0.0.1:8445"),
    "whatsapp": ("VIORUS_WHATSAPP_URL", "http://127.0.0.1:8446"),
    "calendar": ("VIORUS_CALENDAR_URL", "http://127.0.0.1:8447"),
    "cloud_files": ("VIORUS_CLOUD_FILES_URL", "http://127.0.0.1:8448"),
    "notes": ("VIORUS_NOTES_URL", "http://127.0.0.1:8449"),
    "contacts": ("VIORUS_CONTACTS_URL", "http://127.0.0.1:8450"),
    "bookmarks": ("VIORUS_BOOKMARKS_URL", "http://127.0.0.1:8451"),
    "history": ("VIORUS_HISTORY_URL", "http://127.0.0.1:8452"),
    "telephony": ("VIORUS_TELEPHONY_URL", "http://127.0.0.1:8453"),
    "reservations": ("VIORUS_RESERVATIONS_URL", "http://127.0.0.1:8454"),
}

# Observe reads: GET, only whitelisted query params forwarded.
# Execute writes: POST, approval triple attached, connector re-verifies.
# Prepare: POST with no approval (shown, never dialed/sent).
_CONNECTOR_ROUTES: dict[str, _Route] = {
    # Gmail
    "gmail.read_unread": _Route(
        "gmail", "GET", "/digest", query_keys=("hours", "max_results", "account")
    ),
    "gmail.digest_status": _Route("gmail", "GET", "/accounts"),
    # WhatsApp
    "whatsapp.read_digest": _Route(
        "whatsapp", "GET", "/digest", query_keys=("hours", "max_messages", "session_ref")
    ),
    "whatsapp.send_message": _Route(
        "whatsapp",
        "POST",
        "/send",
        body_keys=("recipient", "content", "channel", "session_ref"),
        approval=True,
    ),
    "whatsapp.session_status": _Route("whatsapp", "GET", "/sessions"),
    # Calendar
    "calendar.read_upcoming": _Route(
        "calendar", "GET", "/digest", query_keys=("hours", "max_results", "account")
    ),
    "calendar.digest_status": _Route("calendar", "GET", "/accounts"),
    # Cloud files
    "cloud_files.list_recent": _Route(
        "cloud_files", "GET", "/digest", query_keys=("hours", "max_results", "account")
    ),
    "cloud_files.digest_status": _Route("cloud_files", "GET", "/accounts"),
    # Notes
    "notes.list_recent": _Route(
        "notes", "GET", "/digest", query_keys=("hours", "max_results", "account")
    ),
    "notes.digest_status": _Route("notes", "GET", "/accounts"),
    # Contacts
    "contacts.search": _Route(
        "contacts", "GET", "/search", query_keys=("query", "max_results", "account")
    ),
    "contacts.digest_status": _Route("contacts", "GET", "/accounts"),
    # Bookmarks
    "bookmarks.search": _Route(
        "bookmarks", "GET", "/search", query_keys=("query", "max_results", "source")
    ),
    "bookmarks.digest_status": _Route("bookmarks", "GET", "/sources"),
    # History
    "history.recent": _Route(
        "history", "GET", "/recent", query_keys=("hours", "max_results", "source")
    ),
    "history.digest_status": _Route("history", "GET", "/sources"),
    # Telephony
    "telephony.prepare_call": _Route(
        "telephony", "POST", "/prepare", body_keys=("recipient", "purpose")
    ),
    "telephony.start_call": _Route(
        "telephony", "POST", "/start", body_keys=("recipient", "script"), approval=True
    ),
    # Reservations
    "reservations.search_slots": _Route(
        "reservations", "GET", "/search", query_keys=("venue", "date", "party_size", "session_ref")
    ),
    "reservations.create": _Route(
        "reservations",
        "POST",
        "/create",
        body_keys=("slot_id", "venue", "at", "party_size", "guest_name", "session_ref"),
        approval=True,
    ),
}


def make_executor(
    computer_url: str | None = None,
    smart_home_url: str | None = None,
    connector_urls: dict[str, str] | None = None,
):
    """Return an `execute(step, task)` closure wired to the local services.

    Ports resolve from the env at CALL time, so tests that boot the services on
    free ports are order-independent (an early import never locks in a port).
    `connector_urls` overrides specific services (keyed by service name) for
    tests.
    """
    computer_url = computer_url or os.getenv("VIORUS_COMPUTER_URL", COMPUTER_URL)
    smart_home_url = smart_home_url or os.getenv("VIORUS_SMART_HOME_URL", SMART_HOME_URL)
    connector_urls = connector_urls or {}

    def _dispatch(step, task=None):
        tool = step.tool
        args = (step.result or {}).get("args") or {}

        if tool in (
            "computer.start_remote_session",
            "computer.remote_input",
            "computer.capture_screenshot",
            "computer.list_windows",
            "system.open_app",
        ):
            return _call_computer(tool, args, computer_url)
        if tool.startswith("smart_home."):
            return _call_smart_home(step, task, args, smart_home_url)

        route = _CONNECTOR_ROUTES.get(tool)
        if route is not None:
            env, default = _CONNECTOR_SERVICES[route.service]
            base = connector_urls.get(route.service) or os.getenv(env, default)
            return _call_connector(step, task, args, route, base)

        raise RuntimeError(f"no executor for tool {tool!r} — refused, not guessed")

    return _dispatch


def _call_computer(tool: str, args: dict, base: str) -> dict:
    """Call the computer agent daemon and return a verified result dict."""
    try:
        timeout = float(args.get("timeout") or 45)
    except (TypeError, ValueError):
        timeout = 45.0
    started = time.time()
    if tool == "computer.start_remote_session":
        url = f"{base}/remote/session"
        payload = {
            "device_id": args["device_id"],
            "timeout_minutes": args.get("timeout_minutes", 5),
        }
    elif tool == "computer.remote_input":
        url = f"{base}/remote/input"
        payload = {
            "session_id": args["session_id"],
            "device_id": args["device_id"],
            "action": args["action"],
            "text": args.get("text"),
            "x": args.get("x"),
            "y": args.get("y"),
        }
    elif tool == "computer.capture_screenshot":
        url = f"{base}/screenshot"
        payload = None
    elif tool == "computer.list_windows":
        url = f"{base}/windows"
        payload = None
    elif tool == "system.open_app":
        url = f"{base}/open-app"
        payload = {"name": args.get("name") or args.get("app")}
    else:  # pragma: no cover — guarded by _dispatch
        raise RuntimeError(f"unrouted tool {tool}")

    try:
        with httpx.Client(timeout=timeout) as client:
            if payload is not None:
                resp = client.post(url, json=payload)
            else:
                resp = client.get(url)
    except httpx.RequestError as exc:  # noqa: BLE001
        raise RuntimeError(f"agent daemon unreachable for {tool}: {exc}") from exc

    body = _parse(resp)
    latency_ms = int((time.time() - started) * 1000)
    if resp.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else str(body)
        raise RuntimeError(f"{tool} rejected: {detail}")
    return {"ok": True, "tool": tool, "latency_ms": latency_ms, "result": body}


def _call_smart_home(step, task, args: dict, base: str) -> dict:
    """Call the smart-home connector and return a verified result dict.

    `smart_home.control` is Execute-tier: the engine only runs it after an
    approved ledger record for (task, step, idempotency_key). We forward that
    exact triple so the connector's own gate re-verifies it before a command
    fires — the connector is the last line, never a convenient shortcut.
    """
    tool = step.tool
    task_id = getattr(task, "task_id", None)

    if tool == "smart_home.device_status":
        params = {}
        for key in ("category", "hub_ref"):
            if args.get(key):
                params[key] = args[key]
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{base}/status", params=params)
        except httpx.RequestError as exc:  # noqa: BLE001
            raise RuntimeError(f"smart-home connector unreachable for {tool}: {exc}") from exc
        body = _parse(resp)
        if resp.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else str(body)
            raise RuntimeError(f"{tool} rejected: {detail}")
        return {"ok": True, "tool": tool, "result": body}

    if tool == "smart_home.control":
        payload = {
            "device_id": args.get("device_id"),
            "action": args.get("action"),
            "value": args.get("value"),
            "idempotency_key": step.idempotency_key,
            "task_id": task_id,
            "step_id": step.step_id,
        }
        if args.get("hub_ref"):
            payload["hub_ref"] = args["hub_ref"]
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{base}/control", json=payload)
        except httpx.RequestError as exc:  # noqa: BLE001
            raise RuntimeError(f"smart-home connector unreachable for {tool}: {exc}") from exc
        body = _parse(resp)
        if resp.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else str(body)
            raise RuntimeError(f"{tool} rejected: {detail}")
        return {"ok": True, "tool": tool, "result": body}

    raise RuntimeError(f"no executor for tool {tool!r} — refused, not guessed")


def _call_connector(step, task, args: dict, route: _Route, base: str) -> dict:
    """Call a Phase 6 connector and return a verified result dict.

    Observe reads (GET) only forward whitelisted query args — nothing else.
    Execute writes (POST) forward whitelisted body args plus the exact
    (task, step, idempotency_key) approval triple so the connector's own
    ledger gate re-verifies before anything is transmitted. Prepare (POST)
    forwards body args with no triple — it stages a draft, never sends.
    """
    tool = step.tool
    task_id = getattr(task, "task_id", None)

    try:
        with httpx.Client(timeout=30) as client:
            if route.method == "GET":
                params = {k: args[k] for k in route.query_keys if args.get(k) is not None}
                resp = client.get(f"{base}{route.path}", params=params)
            else:
                payload = {k: args[k] for k in route.body_keys if args.get(k) is not None}
                if route.approval:
                    payload["idempotency_key"] = step.idempotency_key
                    payload["task_id"] = task_id
                    payload["step_id"] = step.step_id
                resp = client.post(f"{base}{route.path}", json=payload)
    except httpx.RequestError as exc:  # noqa: BLE001
        raise RuntimeError(f"{route.service} connector unreachable for {tool}: {exc}") from exc

    body = _parse(resp)
    if resp.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else str(body)
        raise RuntimeError(f"{tool} rejected: {detail}")
    return {"ok": True, "tool": tool, "result": body}


def _parse(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text[:500]}
