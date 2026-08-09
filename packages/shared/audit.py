"""
Vioris audit chain helper.

Pure-Python mirror of the Postgres-side chain logic
(infra/database/migrations/001_init.sql):

    prev_hash = hash of the previous event (or the genesis value)
    hash      = sha256(prev_hash || "<event_id>|<actor>|<action>|<canonical_detail>")

The app uses these helpers to PROPOSE the chain (for tests, dry runs and
offline verification); the database is the source of truth and re-calculates
every hash on INSERT and rejects UPDATE/DELETE.
"""

from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64

SEPARATOR = "|"


def canonical_json(detail: dict) -> str:
    """Stable serialization of a detail dict (sorted keys, compact)."""
    return json.dumps(detail, sort_keys=True, separators=(",", ":"))


def event_payload(event_id: str, actor: str, action: str, detail: dict) -> str:
    """The canonical string hashed for a single audit event."""
    return SEPARATOR.join([event_id, actor, action, canonical_json(detail or {})])


def compute_chain(events, genesis_hash: str = GENESIS_HASH) -> list:
    """Return a copy of `events` with prev_hash/hash filled in, in order.

    `events` must be iterable of objects exposing:
    event_id, actor, action, detail (dict).
    The chain is computed left to right. Useful for offline verification and
    for proposing the chain before a DB write.
    """
    chained = []
    prev = genesis_hash
    for ev in events:
        payload = prev + event_payload(ev.event_id, ev.actor, ev.action, ev.detail)
        ev.prev_hash = prev
        ev.hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        chained.append(ev)
        prev = ev.hash
    return chained


def verify_chain(events, genesis_hash: str = GENESIS_HASH) -> bool:
    """Verify a chained event list from genesis. Returns True if intact."""
    prev = genesis_hash
    for ev in events:
        if ev.prev_hash != prev:
            return False
        payload = prev + event_payload(ev.event_id, ev.actor, ev.action, ev.detail)
        if ev.hash != hashlib.sha256(payload.encode("utf-8")).hexdigest():
            return False
        prev = ev.hash
    return True


def verify_segment(events, expected_chain_end_hash: str) -> bool:
    """Verify a segment links on to a known prior chain hash (no genesis)."""
    if not events:
        return True
    head = events[0]
    if head.prev_hash != expected_chain_end_hash:
        return False
    return verify_chain(events, genesis_hash=head.prev_hash)
