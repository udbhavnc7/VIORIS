"""Memory service integration: HTTP surface for list/correct/delete + opt-in."""

import pytest
from fastapi.testclient import TestClient

from services.memory_service.app.api import app
from services.memory_service.app.store import MemoryStore


@pytest.fixture
def client(tmp_path):
    from services.memory_service.app import api as memory_api

    memory_api._store = MemoryStore(tmp_path / "memory.db")
    with TestClient(app) as c:
        yield c


def test_create_and_list(client):
    r = client.post(
        "/memory",
        json={"category": "people", "content": "Bob is lactose intolerant", "source": "user said"},
    )
    assert r.status_code == 200
    mem = r.json()
    assert mem["category"] == "people"
    assert mem["source"] == "user said"
    assert mem["created_at"]

    listed = client.get("/memory").json()
    assert len(listed) == 1 and listed[0]["memory_id"] == mem["memory_id"]


def test_list_by_category(client):
    client.post("/memory", json={"category": "projects", "content": "p", "source": "s"})
    client.post("/memory", json={"category": "routines", "content": "r", "source": "s"})
    projs = client.get("/memory?category=projects").json()
    assert len(projs) == 1 and projs[0]["category"] == "projects"


def test_sensitive_write_requires_opt_in(client):
    r = client.post(
        "/memory",
        json={"category": "sensitive", "content": "pin 1234", "source": "user said"},
    )
    assert r.status_code == 409
    assert client.get("/memory?category=sensitive").json() == []


def test_sensitive_write_with_opt_in(client):
    r = client.post(
        "/memory",
        json={"category": "sensitive", "content": "pin", "source": "user", "user_opted_in": True},
    )
    assert r.status_code == 200
    assert r.json()["user_opted_in"] is True


def test_correct_memory(client):
    created = client.post("/memory", json={"category": "people", "content": "old", "source": "s"}).json()
    r = client.post(f"/memory/correct/{created['memory_id']}", json={"content": "corrected"})
    assert r.status_code == 200
    assert r.json()["content"] == "corrected"
    assert r.json()["created_at"] == created["created_at"]


def test_delete_memory(client):
    created = client.post("/memory", json={"category": "people", "content": "x", "source": "s"}).json()
    r = client.delete(f"/memory/{created['memory_id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/memory").json() == []