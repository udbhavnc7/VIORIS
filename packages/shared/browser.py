"""Browser Automation — Playwright wrapper for web tasks."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BrowserAction:
    action: str  # navigate, click, type, screenshot, evaluate, scroll
    target: str = ""
    value: str = ""
    selector: str = ""
    url: str = ""
    timeout: int = 30000

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "selector": self.selector,
            "url": self.url,
            "timeout": self.timeout,
        }


@dataclass
class BrowserResult:
    success: bool
    action: str
    data: Any = None
    error: str = ""
    screenshot: bytes = b""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action": self.action,
            "data": self.data,
            "error": self.error,
            "has_screenshot": bool(self.screenshot),
        }


class BrowserSession:
    """Manages a single browser session with Playwright."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._page = None
        self._context = None
        self._pw = None
        self._connected = False

    async def start(self) -> tuple[bool, str]:
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            self._page = await self._context.new_page()
            self._connected = True
            return True, "Browser started"
        except ImportError:
            return False, "Playwright not installed"
        except Exception as e:
            return False, str(e)

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def execute(self, action: BrowserAction) -> BrowserResult:
        if not self._connected:
            return BrowserResult(success=False, action=action.action, error="Not connected")

        try:
            if action.action == "navigate":
                await self._page.goto(action.url, timeout=action.timeout)
                title = await self._page.title()
                return BrowserResult(success=True, action="navigate", data={"title": title, "url": action.url})

            elif action.action == "click":
                await self._page.click(action.selector, timeout=action.timeout)
                return BrowserResult(success=True, action="click", data={"selector": action.selector})

            elif action.action == "type":
                await self._page.fill(action.selector, action.value, timeout=action.timeout)
                return BrowserResult(success=True, action="type", data={"selector": action.selector})

            elif action.action == "screenshot":
                screenshot = await self._page.screenshot(full_page=True)
                return BrowserResult(success=True, action="screenshot", screenshot=screenshot)

            elif action.action == "evaluate":
                result = await self._page.evaluate(action.value)
                return BrowserResult(success=True, action="evaluate", data=result)

            elif action.action == "scroll":
                await self._page.evaluate(f"window.scrollBy(0, {action.value or 500})")
                return BrowserResult(success=True, action="scroll")

            elif action.action == "get_text":
                text = await self._page.text_content(action.selector)
                return BrowserResult(success=True, action="get_text", data=text)

            elif action.action == "get_url":
                url = self._page.url
                return BrowserResult(success=True, action="get_url", data=url)

            else:
                return BrowserResult(success=False, action=action.action, error=f"Unknown action: {action.action}")

        except Exception as e:
            return BrowserResult(success=False, action=action.action, error=str(e))


class BrowserManager:
    """Manages multiple browser sessions."""

    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}

    async def create_session(self, session_id: str, headless: bool = True) -> tuple[bool, str]:
        session = BrowserSession(headless=headless)
        success, msg = await session.start()
        if success:
            self._sessions[session_id] = session
        return success, msg

    async def close_session(self, session_id: str):
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        return self._sessions.get(session_id)

    async def close_all(self):
        for session in self._sessions.values():
            await session.stop()
        self._sessions.clear()

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())
