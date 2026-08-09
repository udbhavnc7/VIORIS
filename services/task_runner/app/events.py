"""
Event hub for the task runner (Phase 2, Prompt 2.3).

Thread-safe pub/sub bridge between the synchronous manager/engine and async
WebSocket subscribers. The manager publishes `pending_approval`, `approved`
and `rejected` events from any thread; each subscriber delivers them onto its
own asyncio queue via `loop.call_soon_threadsafe`, so an async network
endpoint can `await q.get()` without locks.

Events are plain dicts; the API layer shapes them into wire messages.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass


@dataclass(eq=False)
class _Subscriber:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class EventHub:
    """In-process pub/sub. No subscribers -> publish is a no-op."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[_Subscriber] = set()

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> _Subscriber:
        sub = _Subscriber(queue=asyncio.Queue(), loop=loop)
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self, event: dict) -> None:
        """Broadcast a snapshot to every subscriber. Safe from any thread."""
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            sub.loop.call_soon_threadsafe(sub.queue.put_nowait, dict(event))