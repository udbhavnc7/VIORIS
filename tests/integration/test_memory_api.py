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


def test_relationship_is_stored_and_returned(client):
    r = client.post(
        "/memory",
        json={
            "category": "people",
            "content": "Disha is my wife",
            "source": "user said",
            "relationship": "wife",
        },
    )
    assert r.status_code == 200
    assert r.json()["relationship"] == "wife"
    assert client.get("/memory?category=people").json()[0]["relationship"] == "wife"


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


def test_query_routes_event_question_to_episodic_collection(client):
    client.post(
        "/memory",
        json={"category": "routines", "content": "Asked about invoices at 9am", "source": "task_history"},
    )
    client.post(
        "/memory",
        json={"category": "people", "content": "Disha is my wife", "source": "user said"},
    )

    r = client.post("/memory/query", json={"query": "what did I ask yesterday?"})
    assert r.status_code == 200
    body = r.json()
    assert body["memory_type"] == "episodic"
    assert "time_reference" in body["signals"]
    assert body["count"] == 1
    assert body["memories"][0]["content"] == "Asked about invoices at 9am"
    assert body["memories"][0]["memory_type"] == "episodic"


def test_query_routes_fact_question_to_semantic_collection(client):
    client.post(
        "/memory",
        json={"category": "routines", "content": "Opened settings at 9am", "source": "task_history"},
    )
    client.post(
        "/memory",
        json={"category": "preferences", "content": "Dark mode preferred", "source": "user said"},
    )

    r = client.post("/memory/query", json={"query": "what are my preferences?"})
    assert r.status_code == 200
    body = r.json()
    assert body["memory_type"] == "semantic"
    assert body["count"] == 1
    assert body["memories"][0]["content"] == "Dark mode preferred"
    assert body["memories"][0]["memory_type"] == "semantic"


def test_query_applies_keyword_and_time_filters(client):
    client.post("/memory", json={"category": "routines", "content": "Opened Chrome", "source": "task_history"})
    client.post("/memory", json={"category": "routines", "content": "Checked email", "source": "task_history"})

    body = client.post(
        "/memory/query",
        json={"query": "what happened earlier today", "keyword": "chrome", "limit": 5},
    ).json()
    assert body["count"] == 1
    assert "Chrome" in body["memories"][0]["content"]

    from datetime import UTC, datetime

    future = datetime.now(UTC).isoformat()
    body = client.post(
        "/memory/query",
        json={"query": "what happened earlier today", "after": future},
    ).json()
    assert body["count"] == 0


def test_delete_memory(client):
    created = client.post("/memory", json={"category": "people", "content": "x", "source": "s"}).json()
    r = client.delete(f"/memory/{created['memory_id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/memory").json() == []