"""Browser agent integration: daemon HTTP surface + permission gating."""

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import PermissionEngine, register_phase3_browser_tools

from agents.browser.app.agent import BrowserAgent, PermissionBlockedError
from agents.browser.app.daemon import app
from agents.browser.app.driver import FakeDriver

register_phase3_browser_tools()


@pytest.fixture(autouse=True)
def _registry():
    from agents.browser.app import daemon as browser_daemon

    PermissionEngine.reset()
    register_phase3_browser_tools()
    yield
    browser_daemon._agent = None
    PermissionEngine.reset()


@pytest.fixture
def client():
    from agents.browser.app import daemon as browser_daemon

    browser_daemon._agent = BrowserAgent(
        driver=FakeDriver(
            pages={
                "https://example.com": "public docs page",
                "https://captcha.example": "verify you are human — tick the CAPTCHA boxes",
            }
        )
    )
    with TestClient(app) as c:
        yield c


def test_navigate_and_read(client):
    r = client.post("/navigate", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    page = client.get("/page").json()
    assert page["ok"] is True
    assert "public docs page" in page["detail"]["text"]


def test_fill_and_click_flow(client):
    client.post("/navigate", json={"url": "https://example.com"})
    assert client.post("/fill", json={"description": "the email field", "value": "me@x.com"}).json()["ok"]
    out = client.post("/click", json={"description": "the submit button"}).json()
    assert out["ok"] is True


def test_captcha_page_is_surfaced_and_stopped(client):
    """The agent surfaces a CAPTCHA and stops — never attempts to bypass it."""
    r = client.post("/navigate", json={"url": "https://captcha.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["blocked"] == "captcha"
    assert "refusing to automate" in body["note"]


def test_permission_engine_blocks_unregistered_browser_step():
    """CONTRIBUTING: a never-registered browser surface is refused."""
    agent = BrowserAgent(driver=FakeDriver())
    with pytest.raises(PermissionBlockedError) as ei:
        agent.classify_with_gate("browser.harvest_credentials")
    assert "not registered" in str(ei.value)


def test_click_execute_tier_registered_in_engine():
    assert PermissionEngine.classify("browser.click_element").confirmation_required is True