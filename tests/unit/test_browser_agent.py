"""Browser agent unit tests: FakeDriver, login/CAPTCHA surface-and-stop rule."""

import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase3_browser_tools

from agents.browser.app.agent import BrowserAgent, PermissionBlockedError
from agents.browser.app.driver import FakeDriver

register_phase3_browser_tools()


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase3_browser_tools()
    yield
    PermissionEngine.reset()


def agent_with_pages(pages: dict[str, str]) -> BrowserAgent:
    return BrowserAgent(driver=FakeDriver(pages=pages))


class TestNavigate:
    def test_navigate_to_url(self):
        agent = agent_with_pages({"https://example.com": "welcome to example"})
        out = agent.navigate("https://example.com")
        assert out.ok
        assert out.detail["url"] == "https://example.com"

    def test_invalid_url_refused(self):
        agent = agent_with_pages({})
        out = agent.navigate("not a url")
        assert not out.ok
        assert "invalid" in out.note

    def test_https_required(self):
        agent = agent_with_pages({})
        out = agent.navigate("ftp://x.com")
        assert not out.ok


class TestReadPage:
    def test_returns_visible_text(self):
        agent = agent_with_pages({"https://a.com": "Hello world"})
        agent.navigate("https://a.com")
        out = agent.read_page()
        assert out.ok
        assert "Hello world" in out.detail["text"]


class TestFillField:
    def test_fill_stages_value_without_submitting(self):
        agent = agent_with_pages({"https://a.com": "form"})
        agent.navigate("https://a.com")
        out = agent.fill_field("the email field", "me@x.com")
        assert out.ok
        assert "staged" in out.note


class TestClick:
    def test_click_executes_and_records_target(self):
        agent = agent_with_pages({"https://a.com": "form"})
        agent.navigate("https://a.com")
        out = agent.click_element("the submit button")
        assert out.ok
        assert out.detail["element"] == "the submit button"
        assert agent.driver.clicked and agent.driver.clicked[0]["target"] == "submit"


class TestWalls:
    def test_captcha_page_is_surfaced_and_stopped(self):
        agent = agent_with_pages(
            {"https://shop.com": "Please verify you are human. Enter the CAPTCHA below."}
        )
        out = agent.navigate("https://shop.com")
        assert not out.ok
        assert out.blocked == "captcha"
        assert "refusing to automate" in out.note

    def test_login_wall_is_surfaced_and_stopped_on_read(self):
        agent = agent_with_pages({"https://mail.com": "Sign in with your email and password"})
        agent.navigate("https://mail.com")
        out = agent.read_page()
        assert not out.ok
        assert out.blocked == "login"

    def test_click_before_wall_is_blocked_too(self):
        agent = agent_with_pages({"https://x.com": "Log in to continue"})
        agent.navigate("https://x.com")
        out = agent.click_element("the submit button")
        assert not out.ok
        assert out.blocked == "login"

    def test_no_wall_on_clean_page(self):
        agent = agent_with_pages({"https://docs.com": "plain documentation"})
        out = agent.navigate("https://docs.com")
        assert out.ok
        assert out.blocked is None


class TestRegistryGate:
    def test_unregistered_browser_tool_is_blocked(self):
        agent = BrowserAgent(driver=FakeDriver())
        with pytest.raises(PermissionBlockedError) as ei:
            agent.classify_with_gate("browser.harvest_passwords")
        assert "not registered" in str(ei.value)

    def test_click_is_execute_tier_and_requires_confirmation(self):
        reg = PermissionEngine.classify("browser.click_element")
        assert reg.confirmation_required is True

    def test_fill_is_prepare_and_navigate_is_observe(self):
        assert PermissionEngine.classify("browser.fill_field").confirmation_required is False
        assert PermissionEngine.classify("browser.navigate").confirmation_required is False