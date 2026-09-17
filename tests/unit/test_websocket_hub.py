"""Tests for WebSocket hub — real bidirectional communication."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


class FakeWebSocket:
    """In-memory WebSocket for testing."""

    def __init__(self) -> None:
        self.sent: list[dict | str] = []
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive_text(self) -> str:
        # Block forever until cancelled
        await asyncio.Event().wait()
        return ""  # pragma: no cover

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code


@pytest.fixture
def ws():
    return FakeWebSocket()


@pytest.fixture
def ws2():
    return FakeWebSocket()


# ── imports ──────────────────────────────────────────────────────────────────

from services.api.app.websocket import WSClient, WSMessage, WebSocketHub


# ── WSMessage ────────────────────────────────────────────────────────────────


class TestWSMessage:
    def test_to_json(self):
        msg = WSMessage(type="ring", payload={"source": "proactive"})
        raw = msg.to_json()
        data = json.loads(raw)
        assert data["type"] == "ring"
        assert data["payload"] == {"source": "proactive"}
        assert "timestamp" in data

    def test_from_json(self):
        raw = json.dumps({"type": "digest", "payload": {"items": [1, 2]}})
        msg = WSMessage.from_json(raw)
        assert msg.type == "digest"
        assert msg.payload == {"items": [1, 2]}

    def test_from_json_default_payload(self):
        raw = json.dumps({"type": "ping"})
        msg = WSMessage.from_json(raw)
        assert msg.payload == {}

    def test_roundtrip(self):
        msg = WSMessage(type="test", payload={"key": "value"}, sender="a")
        raw = msg.to_json()
        msg2 = WSMessage.from_json(raw)
        assert msg2.type == msg.type
        assert msg2.payload == msg.payload


# ── WebSocketHub connect/disconnect ──────────────────────────────────────────


class TestHubConnection:
    @pytest.mark.asyncio
    async def test_connect(self, ws):
        hub = WebSocketHub()
        client = await hub.connect(ws, "phone-1", "phone")

        assert client.client_id == "phone-1"
        assert client.device_type == "phone"
        assert ws.accepted
        assert "phone-1" in hub._clients
        # Welcome message sent
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "connected"

    @pytest.mark.asyncio
    async def test_connect_with_device_id(self, ws):
        hub = WebSocketHub()
        client = await hub.connect(
            ws, "phone-1", "phone", device_id="dev-abc", authenticated=True
        )
        assert client.device_id == "dev-abc"
        assert client.authenticated

    @pytest.mark.asyncio
    async def test_disconnect(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "phone-1", "phone")
        assert "phone-1" in hub._clients

        await hub.disconnect("phone-1")
        assert "phone-1" not in hub._clients
        assert ws.closed

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self, ws):
        hub = WebSocketHub()
        await hub.disconnect("nope")  # no error

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "c-1", "phone")
        await hub.disconnect("c-1")
        assert ws.closed


# ── send to client ───────────────────────────────────────────────────────────


class TestSendToClient:
    @pytest.mark.asyncio
    async def test_send_to_client(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        result = await hub.send_to_client("p1", {"type": "hello", "payload": {}})
        assert result is True
        # Should have welcome + the message
        assert len(ws.sent) == 2
        assert ws.sent[1]["type"] == "hello"

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_client(self):
        hub = WebSocketHub()
        result = await hub.send_to_client("nope", {"type": "x", "payload": {}})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_handles_exception(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        ws.sent.clear()

        # Make send_json raise
        async def raise_send(data):
            raise ConnectionError("lost")

        ws.send_json = raise_send
        result = await hub.send_to_client("p1", {"type": "x", "payload": {}})
        assert result is False
        # Client should be removed
        assert "p1" not in hub._clients


# ── broadcast ────────────────────────────────────────────────────────────────


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_send_to_phones(self, ws, ws2):
        hub = WebSocketHub()
        await hub.connect(ws, "phone-1", "phone", authenticated=True)
        await hub.connect(ws2, "phone-2", "phone", authenticated=True)

        sent = await hub.send_to_phones({"type": "ping", "payload": {}})
        assert len(sent) == 2
        assert "phone-1" in sent
        assert "phone-2" in sent

    @pytest.mark.asyncio
    async def test_send_to_phones_skips_unauthenticated(self, ws, ws2):
        hub = WebSocketHub()
        await hub.connect(ws, "authed", "phone", authenticated=True)
        await hub.connect(ws2, "unauthed", "phone", authenticated=False)
        ws.sent.clear()  # clear welcome messages
        ws2.sent.clear()

        sent = await hub.send_to_phones({"type": "x", "payload": {}})
        assert len(sent) == 1
        assert ws2.sent == []  # unauthed didn't get it

    @pytest.mark.asyncio
    async def test_send_to_laptops(self, ws, ws2):
        hub = WebSocketHub()
        await hub.connect(ws, "lap-1", "laptop")
        await hub.connect(ws2, "lap-2", "laptop")

        sent = await hub.send_to_laptops({"type": "x", "payload": {}})
        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_broadcast(self, ws, ws2):
        hub = WebSocketHub()
        await hub.connect(ws, "c1", "phone")
        await hub.connect(ws2, "c2", "laptop")
        ws.sent.clear()
        ws2.sent.clear()

        sent = await hub.broadcast({"type": "alert", "payload": {}}, exclude="c1")
        assert len(sent) == 1
        assert "c2" in sent
        assert ws.sent == []  # excluded

    @pytest.mark.asyncio
    async def test_push_to_device(self, ws, ws2):
        hub = WebSocketHub()
        await hub.connect(ws, "c1", "phone", device_id="dev-1")
        await hub.connect(ws2, "c2", "phone", device_id="dev-2")
        ws.sent.clear()
        ws2.sent.clear()

        result = await hub.push_to_device("dev-2", {"type": "ring", "payload": {}})
        assert result is True
        assert len(ws2.sent) == 1  # ring only
        assert ws.sent == []  # not sent to wrong device


# ── receive / handlers ───────────────────────────────────────────────────────


class TestReceive:
    @pytest.mark.asyncio
    async def test_receive_dispatches_to_handler(self, ws):
        hub = WebSocketHub()
        received = []

        def handler(msg: WSMessage):
            received.append(msg)

        hub.on("voice", handler)
        await hub.connect(ws, "p1", "phone")

        raw = json.dumps({"type": "voice", "payload": {"text": "hello"}})
        await hub.receive("p1", raw)

        assert len(received) == 1
        assert received[0].type == "voice"
        assert received[0].payload == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_receive_async_handler(self, ws):
        hub = WebSocketHub()
        received = []

        async def handler(msg: WSMessage):
            received.append(msg)

        hub.on("voice", handler)
        await hub.connect(ws, "p1", "phone")

        raw = json.dumps({"type": "voice", "payload": {}})
        await hub.receive("p1", raw)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_receive_invalid_json(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        # No crash on bad JSON
        await hub.receive("p1", "not-json")

    @pytest.mark.asyncio
    async def test_receive_updates_last_seen(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        client = hub.get_client("p1")
        old_seen = client.last_seen

        await asyncio.sleep(0.01)
        raw = json.dumps({"type": "ping", "payload": {}})
        await hub.receive("p1", raw)
        assert client.last_seen > old_seen

    @pytest.mark.asyncio
    async def test_receive_queues_message(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")

        raw = json.dumps({"type": "test", "payload": {}})
        await hub.receive("p1", raw)

        msg = await hub.get_message(timeout=0.1)
        assert msg is not None
        assert msg.type == "test"


# ── prune_stale ──────────────────────────────────────────────────────────────


class TestPruneStale:
    @pytest.mark.asyncio
    async def test_prune_stale(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        client = hub.get_client("p1")
        client.last_seen = time.time() - 600  # 10 min ago

        stale = hub.prune_stale(timeout=300)
        assert "p1" in stale
        await asyncio.sleep(0.01)  # let disconnect task run
        assert "p1" not in hub._clients

    @pytest.mark.asyncio
    async def test_prune_keeps_fresh(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        stale = hub.prune_stale(timeout=300)
        assert stale == []
        assert "p1" in hub._clients


# ── get_message ──────────────────────────────────────────────────────────────


class TestGetMessage:
    @pytest.mark.asyncio
    async def test_get_message_timeout(self):
        hub = WebSocketHub()
        result = await hub.get_message(timeout=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_returns_queued(self, ws):
        hub = WebSocketHub()
        await hub.connect(ws, "p1", "phone")
        raw = json.dumps({"type": "voice", "payload": {"text": "hi"}})
        await hub.receive("p1", raw)

        msg = await hub.get_message(timeout=0.1)
        assert msg is not None
        assert msg.type == "voice"
