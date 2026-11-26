"""Gateway -> device event bridge: only real typed events are forwarded."""

from __future__ import annotations

import json

import pytest

from services.api_gateway.app import main as gw


class FakeHub:
    def __init__(self):
        self.phones = []
        self.laptops = []

    async def send_to_phones(self, event):
        self.phones.append(event)
        return ["phone-1"]

    async def send_to_laptops(self, event):
        self.laptops.append(event)
        return ["laptop-1"]


@pytest.mark.parametrize(
    "base,expected",
    [
        ("http://127.0.0.1:8421", "ws://127.0.0.1:8421/ws/events"),
        ("http://127.0.0.1:8421/", "ws://127.0.0.1:8421/ws/events"),
        ("https://runner.example/v1", "wss://runner.example/v1/ws/events"),
    ],
)
def test_ws_url_derives_from_runner_url(monkeypatch, base, expected):
    monkeypatch.setattr(gw, "TASK_RUNNER_URL", base)
    assert gw._task_runner_ws_url() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"type": "pending_approval", "approval_id": "a-1"},
        {"type": "approved", "task_id": "t-1"},
        {"type": "rejected", "task_id": "t-1"},
        {"type": "kill_phrase", "task_ids": ["t-1"]},
        {"type": "stopped_all", "task_ids": ["t-1"]},
    ],
)
async def test_typed_events_reach_phones_and_laptops(event):
    hub = FakeHub()
    assert await gw._forward_task_event(json.dumps(event), hub=hub) is True
    assert hub.phones == [event]
    assert hub.laptops == [event]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["not json", json.dumps({"no": "type"}), json.dumps([{"type": "pending_approval"}]), ""],
)
async def test_malformed_or_untyped_events_are_ignored(raw):
    hub = FakeHub()
    assert await gw._forward_task_event(raw, hub=hub) is False
    assert hub.phones == [] and hub.laptops == []
