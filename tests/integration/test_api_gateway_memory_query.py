"""Gateway integration for the episodic/semantic memory query proxy."""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as c:
        pair = c.post("/auth/device/pair", json={"name": "phone"}).json()
        exchange = c.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        c.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        c.device_id = pair["device_id"]
        yield c


def test_gateway_forwards_memory_query_to_the_local_service(client, monkeypatch):
    captured = {}

    async def fake_memory_request(method, path, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {
            "memory_type": "episodic",
            "reason": "question refers to timed events or things that happened",
            "signals": ["event"],
            "count": 1,
            "memories": [
                {
                    "memory_id": "mem_1",
                    "category": "routines",
                    "memory_type": "episodic",
                    "content": "Asked about invoices at 9am",
                }
            ],
        }

    monkeypatch.setattr(gw, "_memory_request", fake_memory_request)
    r = client.post(
        "/v1/memory/query",
        headers=client.auth_headers,
        json={"query": "what did I ask yesterday?", "keyword": "invoices", "limit": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["device"] == client.device_id
    assert body["query"]["memory_type"] == "episodic"
    assert body["query"]["memories"][0]["memory_type"] == "episodic"
    assert captured == {
        "method": "POST",
        "path": "/memory/query",
        "body": {"query": "what did I ask yesterday?", "keyword": "invoices", "limit": 5},
    }


def test_memory_query_requires_a_paired_device(client):
    r = client.post("/v1/memory/query", json={"query": "what did I ask yesterday?"})
    assert r.status_code == 401
