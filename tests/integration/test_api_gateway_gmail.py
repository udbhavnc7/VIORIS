"""Gateway integration for the real Gmail connector (read-only by default)."""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as c:
        pair = c.post("/auth/device/pair", json={"name": "gmail-phone"}).json()
        exchange = c.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        c.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        yield c


def _fake_gmail(captured, response):
    async def fake(method, path, *, params=None, body=None):
        captured.update({"method": method, "path": path, "params": params, "body": body})
        return response

    return fake


def test_gmail_connect_start_defaults_to_read_only(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        gw,
        "_gmail_request",
        _fake_gmail(captured, {"state": "st_1", "authorize_url": "https://accounts.google.com/x", "scopes": "read"}),
    )
    r = client.post("/v1/gmail/connect/start", headers=client.auth_headers, json={})
    assert r.status_code == 200
    assert r.json()["scopes"] == "read"
    assert captured["method"] == "POST"
    assert captured["path"] == "/connect/start"
    assert captured["body"] == {"include_write": False}


def test_gmail_connect_start_can_opt_into_write(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(gw, "_gmail_request", _fake_gmail(captured, {"state": "st_2"}))
    r = client.post(
        "/v1/gmail/connect/start",
        headers=client.auth_headers,
        json={"include_write": True, "redirect_uri": "http://127.0.0.1:8445/oauth/authorized"},
    )
    assert r.status_code == 200
    assert captured["body"]["include_write"] is True
    assert captured["body"]["redirect_uri"] == "http://127.0.0.1:8445/oauth/authorized"


def test_gmail_digest_and_accounts_proxy_with_paired_device(client, monkeypatch):
    captured = []
    calls = []

    async def fake(method, path, *, params=None, body=None):
        captured.append((method, path, params))
        calls.append(1)
        return {"items": []} if path == "/digest" else [{"account": "me@gmail.com"}]

    monkeypatch.setattr(gw, "_gmail_request", fake)
    digest = client.get(
        "/v1/gmail/digest?hours=48&max_results=5&account=me@gmail.com",
        headers=client.auth_headers,
    )
    assert digest.status_code == 200
    assert digest.json()["digest"] == {"items": []}
    accounts = client.get("/v1/gmail/accounts", headers=client.auth_headers)
    assert accounts.json()["accounts"] == [{"account": "me@gmail.com"}]
    assert captured == [
        ("GET", "/digest", {"hours": 48, "max_results": 5, "account": "me@gmail.com"}),
        ("GET", "/accounts", None),
    ]


def test_gmail_disconnect_proxies_account(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(gw, "_gmail_request", _fake_gmail(captured, {"disconnected": True}))
    r = client.post(
        "/v1/gmail/disconnect/me@gmail.com", headers=client.auth_headers
    )
    assert r.status_code == 200
    assert r.json()["result"] == {"disconnected": True}
    assert captured["path"] == "/disconnect/me@gmail.com"


def test_gmail_routes_require_a_paired_device(client):
    assert client.post("/v1/gmail/connect/start", json={}).status_code == 401
    assert client.get("/v1/gmail/accounts").status_code == 401
    assert client.get("/v1/gmail/digest").status_code == 401
    assert client.post("/v1/gmail/disconnect/x").status_code == 401
