"""Gateway integration for opt-in routine suggestions and approval-gated acceptance."""

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
        yield c


def _fake_proxy(captured, response):
    async def fake(method, path, device, body=None, headers=None):
        captured.update({"method": method, "path": path, "body": body})
        return response

    return fake


def test_opt_in_and_suggestions_proxy_to_task_runner(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gw, "_proxy", _fake_proxy(captured, {"opted_in": True, "count": 0, "suggestions": []})
    )
    r = client.post(
        "/v1/routines/opt-in", headers=client.auth_headers, json={"enabled": True}
    )
    assert r.status_code == 200
    assert r.json()["result"]["opted_in"] is True
    assert captured == {"method": "POST", "path": "/routines/opt-in", "body": {"enabled": True}}

    captured.clear()
    r = client.get("/v1/routines/suggestions", headers=client.auth_headers)
    assert r.status_code == 200
    assert captured["path"] == "/routines/suggestions"


def test_create_proxies_pattern_without_approving(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gw,
        "_proxy",
        _fake_proxy(
            captured,
            {"task_id": "task_1", "status": "waiting_approval", "approval_id": "apr_1", "diff_card": {}},
        ),
    )
    r = client.post(
        "/v1/routines/create",
        headers=client.auth_headers,
        json={"pattern_id": "seq_abc_0", "sequence": ["system.open_app"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["approval_id"] == "apr_1"
    assert body["task"]["status"] == "waiting_approval"
    assert captured["body"] == {"pattern_id": "seq_abc_0", "sequence": ["system.open_app"]}


def test_routine_routes_require_a_paired_device(client):
    assert client.post("/v1/routines/opt-in", json={"enabled": True}).status_code == 401
    assert client.get("/v1/routines/suggestions").status_code == 401
    assert client.post("/v1/routines/create", json={"pattern_id": "x"}).status_code == 401
