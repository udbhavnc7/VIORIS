"""
Browser agent — driver layer (Phase 3, Prompt 3.2).

`PlaywrightDriver` drives an ISOLATED Chromium profile via Playwright: a fresh
user-data-dir under vioris_data, never the user's personal logged-in session
unless the caller explicitly points user_data_dir at one.

Playwright is imported lazily so the package (and tests) still load when it's
not installed; the browser agent fails loudly only when a real browser action
is requested without a backend.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROFILE_DIR = Path("vioris_data/browser_profile")


class BrowserBackendError(Exception):
    """No usable browser backend for the requested action."""


@dataclass
class Element:
    """A resolvable element on the page."""

    selector: str
    role: str = ""
    name: str = ""
    placeholder: str = ""
    tag: str = ""


@dataclass
class PageSnapshot:
    url: str
    title: str
    text: str
    markers: list[str] = field(default_factory=list)  # e.g. ['captcha', 'login']


class BrowserDriver:
    """Interface the agent talks to. Playwright impl below; tests use FakeDriver."""

    def navigate(self, url: str) -> PageSnapshot:  # pragma: no cover - interface
        raise NotImplementedError

    def read_page(self) -> PageSnapshot:  # pragma: no cover - interface
        raise NotImplementedError

    def fill(self, element: Element, value: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def click(self, element: Element) -> PageSnapshot:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class FakeDriver(BrowserDriver):
    """Deterministic in-memory page for tests. No browser needed."""

    def __init__(self, pages: dict[str, str] | None = None, elements: dict[str, str] | None = None) -> None:
        self._pages: dict[str, str] = dict(pages or {})
        # selector -> value
        self._field_values: dict[str, str] = {}
        self._elements: dict[str, Any] = dict(elements or {})
        self._current_url: str | None = None
        self.clicked: list[dict] = []

    def _snapshot(self) -> PageSnapshot:
        text = self._pages.get(self._current_url or "", "")
        markers = []
        low = text.lower()
        if any(k in low for k in ("captcha", "i'm not a robot", "verify you are human")):
            markers.append("captcha")
        if any(k in low for k in ("log in", "login", "sign in", "password")):
            markers.append("login")
        return PageSnapshot(url=self._current_url or "", title=self._current_url or "", text=text, markers=markers)

    def navigate(self, url: str) -> PageSnapshot:
        self._current_url = url
        self._pages.setdefault(url, "hello browser page")
        return self._snapshot()

    def read_page(self) -> PageSnapshot:
        if not self._current_url:
            raise BrowserBackendError("no page loaded")
        return self._snapshot()

    def fill(self, element: Element, value: str) -> None:
        key = element.name or element.selector
        self._elements[key] = value
        self._field_values[key] = value

    def click(self, element: Element) -> PageSnapshot:
        if not self._current_url:
            raise BrowserBackendError("no page loaded")
        key = element.name or element.selector
        self.clicked.append({"url": self._current_url, "target": key})
        # A click may reveal a login/captcha wall on the next page state.
        self._pages[self._current_url] = self._pages.get(self._current_url, "") + " >> clicked"
        return self._snapshot()

    def close(self) -> None:
        self._current_url = None


class PlaywrightDriver(BrowserDriver):
    def __init__(self, profile_dir: str | Path | None = None) -> None:
        self._owned_page = None
        self._profile_dir = Path(profile_dir or PROFILE_DIR)
        self._pw = None
        self._browser = None
        self._context = None

    def _ensure(self):
        if self._context is not None:
            return self._context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserBackendError(
                "Playwright is not installed (pip install playwright && "
                "playwright install chromium). The automation browser is unavailable."
            ) from exc

        # Isolated automation profile: fresh user-data-dir under vioris_data,
        # separate from the user's personal logged-in browser.
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_data_dir=str(self._profile_dir))
        return self._context

    def _page(self):
        ctx = self._ensure()
        page = ctx.new_page()
        return page

    def navigate(self, url: str) -> PageSnapshot:
        page = self._page()
        page.goto(url)
        snap = self._snap(page)
        page.close()
        return snap

    def read_page(self) -> PageSnapshot:
        page = self._page()
        snap = self._snap(page)
        page.close()
        return snap

    def fill(self, element: Element, value: str) -> None:
        page = self._page()
        loc = self._resolve(page, element)
        loc.fill(value)
        page.close()

    def click(self, element: Element) -> PageSnapshot:
        page = self._page()
        loc = self._resolve(page, element)
        loc.click()
        snap = self._snap(page)
        page.close()
        return snap

    def _resolve(self, page, element: Element):
        if element.name:
            loc = page.get_by_label(element.name, exact=False)
            if loc.count():
                return loc
        if element.placeholder:
            return page.get_by_placeholder(element.placeholder)
        if element.tag:  # heuristic: first element of a tag matching text
            loc = page.locator(element.tag)
            if element.name:
                loc = loc.filter(has_text=element.name)
            return loc.first
        return page.locator(element.selector)

    @staticmethod
    def _snap(page) -> PageSnapshot:
        text = page.locator("body").inner_text()
        markers = []
        low = text.lower()
        if any(k in low for k in ("captcha", "i'm not a robot", "verify you are human")):
            markers.append("captcha")
        if any(k in low for k in ("log in", "login", "sign in", "password")):
            markers.append("login")
        return PageSnapshot(url=page.url, title=page.title(), text=text, markers=markers)

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None


DriverFactory = Callable[[], BrowserDriver]


def default_playwright() -> BrowserDriver:
    return PlaywrightDriver()