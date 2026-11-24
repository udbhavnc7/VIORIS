"""
Demo connectors — sample data, zero delivery.

Purpose: let every OBSERVE workflow run end-to-end on a laptop with no
accounts connected, while never faking a live integration. Every payload is
marked `demo: true`. Every write endpoint refuses with ok:false and a reason,
so an approved Execute step fails honestly instead of claiming it sent.

Run:
    uvicorn services.demo_connectors.app:app --port 8460

Point the gateway at it with:
    VIORIS_DEMO_CONNECTORS=1
    VIORUS_GMAIL_URL=http://127.0.0.1:8460/gmail
    ... (see run_vioris.py)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI

app = FastAPI(title="Vioris Demo Connectors")

_DEMO = {
    "demo": True,
    "notice": "Sample data. No account is connected and nothing is delivered.",
}


def _iso(hours: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds")


_CONTACTS = [
    {"name": "Disha", "email": "disha@example.com", "phone": "+1 555 0100", "aliases": ["wife"]},
    {"name": "RazorClub Ops", "email": "ops@razorclub.example", "phone": "", "aliases": ["razorclub"]},
    {"name": "Shravan GK", "email": "shravan.gk@example.com", "phone": "+1 555 0111", "aliases": []},
    {"name": "Shravan G", "email": "shravan.g@example.com", "phone": "+1 555 0112", "aliases": []},
]


def _fail(reason: str) -> dict[str, Any]:
    return {"ok": False, "demo": True, "error": reason}


# ─── health (one per prefix so the gateway's probe resolves) ─────────────────


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "vioris-demo-connectors",
        "mode": "demo",
        "prefixes": [
            "gmail",
            "calendar",
            "whatsapp",
            "contacts",
            "notes",
            "cloud_files",
            "bookmarks",
            "history",
            "telephony",
            "reservations",
            "smart_home",
        ],
    }


@app.get("/{service}/health")
def service_health(service: str) -> dict:
    return {"ok": True, "service": service, "mode": "demo", **_DEMO}


# ─── Gmail (read-only) ──────────────────────────────────────────────────────


@app.get("/gmail/accounts")
def gmail_accounts() -> dict:
    return {
        "accounts": [{"account": "demo@example.com", "scope": "gmail.readonly", "demo": True}],
        **_DEMO,
    }


@app.get("/gmail/digest")
def gmail_digest(hours: int = 24, max_results: int = 5, account: str | None = None) -> dict:
    items = [
        {
            "from": "RazorClub Ops",
            "subject": "Tomorrow's hiring review",
            "summary": "Important meeting tomorrow on next hires.",
            "received_at": _iso(-2),
            "unread": True,
        },
        {
            "from": "Vioris",
            "subject": "Your weekly permission digest",
            "summary": "3 observe actions, 1 approval, 0 critical actions.",
            "received_at": _iso(-9),
            "unread": True,
        },
    ]
    return {"ok": True, "items": items[:max_results], "unread": len(items), **_DEMO}


@app.post("/gmail/react")
def gmail_react(payload: dict) -> dict:
    return _fail(
        "Demo mode: no Gmail account is connected, so nothing was starred, "
        "labelled, or marked read."
    )


@app.post("/gmail/reply")
def gmail_reply(payload: dict) -> dict:
    return _fail(
        "Demo mode: no Gmail account is connected, so no reply was sent."
    )


# ─── Calendar ───────────────────────────────────────────────────────────────


@app.get("/calendar/accounts")
def calendar_accounts() -> dict:
    return {"accounts": [{"account": "demo@example.com", "demo": True}], **_DEMO}


@app.get("/calendar/digest")
def calendar_digest(hours: int = 24, max_results: int = 5, account: str | None = None) -> dict:
    items = [
        {
            "title": "Hiring review",
            "start": _iso(20),
            "end": _iso(21),
            "location": "Zoom",
            "attendees": ["RazorClub Ops", "Disha"],
        },
        {
            "title": "Focus block",
            "start": _iso(25),
            "end": _iso(27),
            "location": "",
            "attendees": [],
        },
    ]
    return {"ok": True, "items": items[:max_results], **_DEMO}


# ─── WhatsApp ───────────────────────────────────────────────────────────────


@app.get("/whatsapp/sessions")
def whatsapp_sessions() -> dict:
    return {"sessions": [], "transport": "none", **_DEMO}


@app.get("/whatsapp/digest")
def whatsapp_digest(
    hours: int = 24, max_messages: int = 5, session_ref: str | None = None
) -> dict:
    items = [
        {
            "from": "Disha",
            "text": "When are you reaching home?",
            "received_at": _iso(-1),
            "unread": True,
        },
        {
            "from": "Team LUME",
            "text": "Phase two notes are in the shared folder.",
            "received_at": _iso(-4),
            "unread": True,
        },
    ]
    return {"ok": True, "items": items[:max_messages], "transport": "none", **_DEMO}


@app.post("/whatsapp/send")
def whatsapp_send(payload: dict) -> dict:
    return _fail(
        "Demo mode: no WhatsApp session is linked, so nothing was sent. "
        "Link a transport before approving a send."
    )


# ─── Contacts ───────────────────────────────────────────────────────────────


@app.get("/contacts/accounts")
def contacts_accounts() -> dict:
    return {"accounts": [{"account": "local", "demo": True}], **_DEMO}


@app.get("/contacts/search")
def contacts_search(
    query: str = "", max_results: int = 5, account: str | None = None
) -> dict:
    q = query.strip().lower()
    matches = [
        c
        for c in _CONTACTS
        if not q or q in c["name"].lower() or any(q in a for a in c["aliases"])
    ]
    return {"ok": True, "results": matches[:max_results], **_DEMO}


# ─── Notes / cloud files / bookmarks / history ──────────────────────────────


@app.get("/notes/accounts")
@app.get("/cloud_files/accounts")
def document_accounts() -> dict:
    return {"accounts": [{"account": "demo", "demo": True}], **_DEMO}


@app.get("/notes/digest")
def notes_digest(hours: int = 24, max_results: int = 5, account: str | None = None) -> dict:
    return {
        "ok": True,
        "items": [
            {"title": "LUME phase two notes", "updated_at": _iso(-5)},
            {"title": "Hiring rubric", "updated_at": _iso(-12)},
        ][:max_results],
        **_DEMO,
    }


@app.get("/cloud_files/digest")
def files_digest(hours: int = 24, max_results: int = 5, account: str | None = None) -> dict:
    return {
        "ok": True,
        "items": [
            {"name": "LUME-phase2.pdf", "updated_at": _iso(-5)},
            {"name": "Hiring-plan.docx", "updated_at": _iso(-12)},
        ][:max_results],
        **_DEMO,
    }


@app.get("/bookmarks/sources")
@app.get("/history/sources")
def source_list() -> dict:
    return {"sources": [{"source": "demo", "demo": True}], **_DEMO}


@app.get("/bookmarks/search")
def bookmarks_search(query: str = "", max_results: int = 5, source: str | None = None) -> dict:
    return {
        "ok": True,
        "results": [
            {"title": "Local-first architecture notes", "url": "https://example.com/local-first"},
        ][:max_results],
        **_DEMO,
    }


@app.get("/history/recent")
def history_recent(hours: int = 24, max_results: int = 5, source: str | None = None) -> dict:
    return {
        "ok": True,
        "items": [
            {"title": "Tailscale ACL reference", "url": "https://tailscale.com/kb", "visited_at": _iso(-3)},
        ][:max_results],
        **_DEMO,
    }


# ─── Telephony (prepare only) ───────────────────────────────────────────────


@app.post("/telephony/prepare")
def telephony_prepare(payload: dict) -> dict:
    return {
        "ok": True,
        "prepared": {
            "recipient": payload.get("recipient"),
            "purpose": payload.get("purpose"),
            "dialer_prefilled": False,
        },
        "note": "Demo mode: a call script is prepared but no call is placed.",
        **_DEMO,
    }


@app.post("/telephony/start")
def telephony_start(payload: dict) -> dict:
    return _fail("Demo mode: the dialer is not connected, so no call was placed.")


# ─── Reservations ───────────────────────────────────────────────────────────


@app.get("/reservations/search")
def reservations_search(
    venue: str = "",
    date: str | None = None,
    party_size: int = 2,
    session_ref: str | None = None,
) -> dict:
    return {
        "ok": True,
        "venue": venue,
        "party_size": party_size,
        "slots": [
            {"slot_id": "demo-1830", "at": _iso(30), "label": "18:30"},
            {"slot_id": "demo-1930", "at": _iso(31), "label": "19:30"},
        ],
        **_DEMO,
    }


@app.post("/reservations/create")
def reservations_create(payload: dict) -> dict:
    return _fail(
        "Demo mode: reservation search works, but booking is not connected. Nothing was booked."
    )


# ─── Smart home ─────────────────────────────────────────────────────────────


@app.get("/smart_home/status")
def smart_home_status(category: str | None = None, hub_ref: str | None = None) -> dict:
    return {
        "ok": True,
        "devices": [
            {"device_id": "light.living_room", "name": "Living room light", "state": "off"},
            {"device_id": "climate.hall", "name": "Hall thermostat", "state": "21.5C"},
        ],
        **_DEMO,
    }


@app.post("/smart_home/control")
def smart_home_control(payload: dict) -> dict:
    return _fail("Demo mode: no smart-home hub is linked, so nothing was switched.")
