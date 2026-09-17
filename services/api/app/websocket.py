"""
WebSocket server for real-time phone ↔ laptop communication.

This is the REAL implementation — holds actual WebSocket connections,
sends messages to connected devices, and handles bidirectional commands.

Usage:
    hub = WebSocketHub()
    # In the FastAPI WebSocket endpoint:
    await hub.connect(websocket, client_id, device_type)
    # To push to phones:
    await hub.send_to_phones({"type": "ring", "payload": {...}})
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


@dataclass
class WSClient:
    """A connected WebSocket client."""

    client_id: str
    device_type: str  # "phone" or "laptop"
    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    authenticated: bool = False
    device_id: str | None = None  # paired device ID from JWT


@dataclass
class WSMessage:
    """A message sent over WebSocket."""

    type: str
    payload: dict
    sender: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "payload": self.payload,
                "sender": self.sender,
                "timestamp": self.timestamp,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> WSMessage:
        d = json.loads(raw)
        return cls(
            type=d["type"],
            payload=d.get("payload", {}),
            sender=d.get("sender", ""),
        )


class WebSocketHub:
    """Manages WebSocket connections for phone ↔ laptop communication.

    Unlike a pure registry, this hub HOLDS the WebSocket objects and can
    actually send messages to connected devices.
    """

    def __init__(self) -> None:
        self._clients: dict[str, WSClient] = {}
        self._handlers: dict[str, list[Callable]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()

    async def connect(
        self,
        websocket: WebSocket,
        client_id: str,
        device_type: str,
        device_id: str | None = None,
        authenticated: bool = False,
    ) -> WSClient:
        """Accept a WebSocket connection and register the client."""
        await websocket.accept()
        client = WSClient(
            client_id=client_id,
            device_type=device_type,
            websocket=websocket,
            device_id=device_id,
            authenticated=authenticated,
        )
        self._clients[client_id] = client
        logger.info("WebSocket connected: %s (%s)", client_id, device_type)

        # Send welcome message
        await self.send_to_client(
            client_id,
            {
                "type": "connected",
                "payload": {
                    "client_id": client_id,
                    "device_type": device_type,
                    "server_time": time.time(),
                },
            },
        )
        return client

    async def disconnect(self, client_id: str) -> None:
        """Remove a client and close its connection."""
        client = self._clients.pop(client_id, None)
        if client:
            try:
                await client.websocket.close()
            except Exception:
                pass
            logger.info("WebSocket disconnected: %s", client_id)

    def get_client(self, client_id: str) -> WSClient | None:
        return self._clients.get(client_id)

    def get_phone_clients(self) -> list[WSClient]:
        return [c for c in self._clients.values() if c.device_type == "phone"]

    def get_laptop_clients(self) -> list[WSClient]:
        return [c for c in self._clients.values() if c.device_type == "laptop"]

    def get_authenticated_phones(self) -> list[WSClient]:
        return [
            c
            for c in self._clients.values()
            if c.device_type == "phone" and c.authenticated
        ]

    def on(self, message_type: str, handler: Callable) -> None:
        """Register a handler for a message type."""
        self._handlers.setdefault(message_type, []).append(handler)

    async def receive(self, client_id: str, raw: str) -> None:
        """Process an incoming message from a client."""
        try:
            msg = WSMessage.from_json(raw)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Invalid message from %s: %s", client_id, exc)
            return

        msg.sender = client_id
        client = self._clients.get(client_id)
        if client:
            client.last_seen = time.time()

        # Dispatch to registered handlers
        for handler in self._handlers.get(msg.type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(msg)
                else:
                    handler(msg)
            except Exception as exc:
                logger.error("Handler error for %s: %s", msg.type, exc)

        # Also put on the queue for any consumer
        await self._message_queue.put(msg)

    async def send_to_client(self, client_id: str, data: dict) -> bool:
        """Send a message to a specific client. Returns True if sent."""
        client = self._clients.get(client_id)
        if not client:
            return False
        try:
            if isinstance(data, dict):
                await client.websocket.send_json(data)
            else:
                await client.websocket.send_text(str(data))
            return True
        except Exception as exc:
            logger.warning("Failed to send to %s: %s", client_id, exc)
            await self.disconnect(client_id)
            return False

    async def send_to_phones(self, data: dict) -> list[str]:
        """Broadcast a message to all connected (authenticated) phones."""
        sent = []
        for client in self.get_authenticated_phones():
            if await self.send_to_client(client.client_id, data):
                sent.append(client.client_id)
        return sent

    async def send_to_laptops(self, data: dict) -> list[str]:
        """Broadcast a message to all connected laptops."""
        sent = []
        for client in self.get_laptop_clients():
            if await self.send_to_client(client.client_id, data):
                sent.append(client.client_id)
        return sent

    async def broadcast(self, data: dict, exclude: str | None = None) -> list[str]:
        """Broadcast to all connected clients except the excluded one."""
        sent = []
        for cid, client in self._clients.items():
            if cid != exclude:
                if await self.send_to_client(cid, data):
                    sent.append(cid)
        return sent

    async def push_to_device(self, device_id: str, data: dict) -> bool:
        """Push a message to a specific paired device (by device_id, not client_id)."""
        for client in self._clients.values():
            if client.device_id == device_id:
                return await self.send_to_client(client.client_id, data)
        return False

    @property
    def connected_count(self) -> int:
        return len(self._clients)

    def prune_stale(self, timeout: float = 300) -> list[str]:
        """Remove clients that haven't sent a message in `timeout` seconds."""
        now = time.time()
        stale = [
            cid
            for cid, c in self._clients.items()
            if now - c.last_seen > timeout
        ]
        for cid in stale:
            asyncio.create_task(self.disconnect(cid))
        return stale

    async def get_message(self, timeout: float | None = None) -> WSMessage | None:
        """Get the next message from the queue (for consumers)."""
        try:
            return await asyncio.wait_for(
                self._message_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
