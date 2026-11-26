"""API gateway knowledge proxy: device-auth only, forwards to local service.

The knowledge service itself remains the sole owner of the approval-root gate;
these tests assert the gateway neither invents results nor bypasses a 403.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _AsyncClient:
    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    async def request(self, method, url, json=None, timeout=None):
        self._calls.append((method, url, json))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(*item)

    async def get(self, url, timeout=None):
        return await self.request("GET", url, timeout=timeout)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gw, "KNOWLEDGE_URL", "http://127.0.0.1:18999")
    gw._store = PairingStore(secret="test-secret")
    gw._pipeline = None
    calls = []
    responses = []
    monkeypatch.setattr(gw, "_client", lambda: _AsyncClient(responses, calls))
    with TestClient(gw.app) as test_client:
        pair = test_client.post("/auth/device/pair", json={"name": "phone"}).json()
        exchange = test_client.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        test_client.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        test_client.calls = calls
        test_client.responses = responses
        yield test_client
    gw._pipeline = None


def _assert_auth(client, method, path, body=None):
    response = client.request(method, path, json=body)
    assert response.status_code == 401


def test_knowledge_routes_require_device_auth(client):
    _assert_auth(client, "GET", "/v1/knowledge/documents")
    _assert_auth(client, "POST", "/v1/knowledge/query", {"question": "why?"})
    _assert_auth(client, "POST", "/v1/knowledge/ingest", {"title": "x", "path": "x.md"})


def test_query_forwards_to_local_service(client):
    client.responses.append((200, {"found": True, "answer": "Because local Whisper."}))
    response = client.post(
        "/v1/knowledge/query",
        headers=client.auth_headers,
        json={"question": "why local Whisper?", "top_k": 3},
    )
    assert response.status_code == 200
    assert response.json()["result"]["found"] is True
    method, url, body = client.calls[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:18999/knowledge/query"
    assert body == {"question": "why local Whisper?", "top_k": 3}


def test_ingest_forwards_exact_title_and_path(client):
    client.responses.append((200, {"chunks_indexed": 2}))
    response = client.post(
        "/v1/knowledge/ingest",
        headers=client.auth_headers,
        json={"title": "Decisions", "path": "/approved/decisions.md"},
    )
    assert response.status_code == 200
    assert client.calls[-1][2] == {"title": "Decisions", "path": "/approved/decisions.md"}


def test_documents_endpoint_returns_service_list(client):
    client.responses.append((200, [{"title": "Decisions"}]))
    response = client.get("/v1/knowledge/documents", headers=client.auth_headers)
    assert response.status_code == 200
    assert response.json()["documents"] == [{"title": "Decisions"}]


def test_service_403_is_not_converted_into_success(client):
    client.responses.append((403, {"detail": "path is not approved"}))
    response = client.post(
        "/v1/knowledge/ingest",
        headers=client.auth_headers,
        json={"title": "bad", "path": "/private/x.md"},
    )
    assert response.status_code == 403
    assert "not approved" in response.json()["detail"]


def test_unreachable_service_returns_503(client):
    from httpx import ConnectError

    client.responses.append(ConnectError("refused"))
    response = client.get("/v1/knowledge/documents", headers=client.auth_headers)
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]
