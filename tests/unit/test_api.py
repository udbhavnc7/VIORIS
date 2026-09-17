"""Tests for WebSocket hub and API endpoints."""

import asyncio
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from services.api.app.websocket import WSClient, WSMessage, WebSocketHub
from services.api.app.main import app


class FakeWebSocket:
    """In-memory WebSocket for testing."""

    def __init__(self):
        self.sent = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        await asyncio.Event().wait()

    async def close(self, code=None, reason=None):
        self.closed = True


# --- WebSocket Hub Tests ---


class TestWSMessage:
    def test_to_json(self):
        msg = WSMessage(type="ping", payload={"key": "value"}, sender="client1")
        data = json.loads(msg.to_json())
        assert data["type"] == "ping"
        assert data["payload"] == {"key": "value"}
        assert data["sender"] == "client1"

    def test_from_json(self):
        raw = json.dumps({"type": "audio", "payload": {"data": "chunk1"}, "sender": "phone1"})
        msg = WSMessage.from_json(raw)
        assert msg.type == "audio"
        assert msg.payload == {"data": "chunk1"}

    def test_from_json_defaults(self):
        raw = json.dumps({"type": "test"})
        msg = WSMessage.from_json(raw)
        assert msg.payload == {}
        assert msg.sender == ""


class TestWSClient:
    @pytest.mark.asyncio
    async def test_create(self):
        ws = FakeWebSocket()
        client = WSClient(client_id="c1", device_type="phone", websocket=ws)
        assert client.client_id == "c1"
        assert client.device_type == "phone"
        assert client.authenticated is False

    @pytest.mark.asyncio
    async def test_default_times(self):
        ws = FakeWebSocket()
        client = WSClient(client_id="c1", device_type="laptop", websocket=ws)
        assert client.connected_at > 0
        assert client.last_seen > 0


class TestWebSocketHub:
    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws, "phone1", "phone")
        assert hub.connected_count == 1
        await hub.disconnect("phone1")
        assert hub.connected_count == 0

    @pytest.mark.asyncio
    async def test_get_client(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws, "phone1", "phone")
        client = hub.get_client("phone1")
        assert client is not None
        assert client.device_type == "phone"

    @pytest.mark.asyncio
    async def test_get_phone_clients(self):
        ws1, ws2, ws3 = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws1, "phone1", "phone")
        await hub.connect(ws2, "laptop1", "laptop")
        await hub.connect(ws3, "phone2", "phone")
        phones = hub.get_phone_clients()
        assert len(phones) == 2

    @pytest.mark.asyncio
    async def test_get_laptop_clients(self):
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws1, "phone1", "phone")
        await hub.connect(ws2, "laptop1", "laptop")
        laptops = hub.get_laptop_clients()
        assert len(laptops) == 1

    @pytest.mark.asyncio
    async def test_broadcast(self):
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws1, "phone1", "phone")
        await hub.connect(ws2, "phone2", "phone")
        sent = await hub.broadcast({"type": "test", "payload": {}})
        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_broadcast_exclude(self):
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws1, "phone1", "phone")
        await hub.connect(ws2, "phone2", "phone")
        sent = await hub.broadcast({"type": "test", "payload": {}}, exclude="phone1")
        assert len(sent) == 1
        assert "phone2" in sent

    @pytest.mark.asyncio
    async def test_prune_stale(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws, "phone1", "phone")
        client = hub.get_client("phone1")
        client.last_seen = time.time() - 600
        stale = hub.prune_stale(timeout=300)
        assert len(stale) == 1

    @pytest.mark.asyncio
    async def test_prune_keeps_active(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        await hub.connect(ws, "phone1", "phone")
        stale = hub.prune_stale(timeout=300)
        assert len(stale) == 0
        assert hub.connected_count == 1

    @pytest.mark.asyncio
    async def test_receive_calls_handlers(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        received = []
        hub.on("ping", lambda msg: received.append(msg))
        await hub.connect(ws, "client1", "phone")
        await hub.receive("client1", json.dumps({"type": "ping", "payload": {}}))
        assert len(received) == 1
        assert received[0].type == "ping"

    @pytest.mark.asyncio
    async def test_receive_async_handler(self):
        ws = FakeWebSocket()
        hub = WebSocketHub()
        received = []

        async def handler(msg):
            received.append(msg)

        hub.on("test", handler)
        await hub.connect(ws, "client1", "phone")
        await hub.receive("client1", json.dumps({"type": "test", "payload": {}}))
        assert len(received) == 1


# --- API Endpoint Tests ---


client = TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"


class TestVoiceEndpoint:
    def test_process_simple_command(self):
        response = client.post("/api/voice", json={"text": "check email"})
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "decomposed" in data
        assert "trust_level" in data

    def test_process_with_call_id(self):
        response = client.post("/api/voice", json={"text": "read email", "call_id": "c1"})
        assert response.status_code == 200
        data = response.json()
        assert data["call_id"] == "c1"

    def test_process_empty_text(self):
        response = client.post("/api/voice", json={"text": ""})
        assert response.status_code == 200


class TestApprovalEndpoint:
    def _create_approval(self, action_id: str):
        from services.api.app.main import _approvals
        _approvals[action_id] = {"action_id": action_id, "status": "pending", "created_at": datetime.utcnow().isoformat()}

    def test_approve_action(self):
        self._create_approval("test_action_1")
        response = client.post("/api/approve", json={"action_id": "test_action_1", "approved": True})
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_reject_action(self):
        self._create_approval("test_action_2")
        response = client.post("/api/approve", json={"action_id": "test_action_2", "approved": False})
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    def test_approve_nonexistent(self):
        response = client.post("/api/approve", json={"action_id": "nonexistent", "approved": True})
        assert response.status_code == 404

    def test_get_approval_status(self):
        self._create_approval("test_action_3")
        response = client.get("/api/approve/test_action_3")
        assert response.status_code == 200

    def test_get_approval_not_found(self):
        response = client.get("/api/approve/nonexistent")
        assert response.status_code == 404


class TestCallEndpoints:
    def test_start_call(self):
        response = client.post("/api/call/start", json={"source": "incoming", "caller_name": "Disha"})
        assert response.status_code == 200
        data = response.json()
        assert "call_id" in data
        assert data["status"] == "ringing"

    def test_get_call_status(self):
        response = client.get("/api/call/test_call_1")
        assert response.status_code == 200
        data = response.json()
        assert data["call_id"] == "test_call_1"

    def test_end_call(self):
        response = client.post("/api/call/test_call_1/end")
        assert response.status_code == 200
        assert response.json()["status"] == "ended"


class TestDigestEndpoint:
    def test_empty_digest(self):
        response = client.post("/api/digest", json={"call_id": "c1", "items": []})
        assert response.status_code == 200
        assert response.json()["spoken"] == "Nothing new."

    def test_digest_with_items(self):
        items = [
            {"item_type": "email", "summary": "Meeting at 3", "source": "Boss", "actionable": False}
        ]
        response = client.post("/api/digest", json={"call_id": "c1", "items": items})
        assert response.status_code == 200
        assert "Meeting at 3" in response.json()["spoken"]


class TestReactEndpoint:
    def test_valid_reaction(self):
        response = client.post("/api/react?email_id=e1&reaction=%F0%9F%91%8D&sender=Disha")
        assert response.status_code == 200
        assert response.json()["reaction"] == "👍"

    def test_invalid_reaction(self):
        response = client.post("/api/react?email_id=e1&reaction=fire&sender=Disha")
        assert response.status_code == 400
