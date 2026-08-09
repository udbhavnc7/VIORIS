"""
Browser agent (Phase 3, Prompt 3.2).

Drives an isolated automation browser (never the user's personal session unless
explicitly configured). Tools:

  - browser.navigate      (observe)
  - browser.read_page     (observe)
  - browser.fill_field    (prepare — stages input, nothing external)
  - browser.click_element (execute — externally visible, approval required)

Hard rule: the agent NEVER attempts to bypass a CAPTCHA or login wall. When a
page presents either, the agent surfaces it to the user and STOPS — it does
not try OCR solvers, hidden fields, headless-evasion, or credential stuffing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from packages.shared.permission_engine import UnknownToolError

from .driver import BrowserDriver, Element, FakeDriver

logger = logging.getLogger(__name__)

# Substrings that indicate a login wall vs. a CAPTCHA challenge.
LOGIN_MARKERS = ("log in", "login", "sign in", "password", "username", "email address")
CAPTCHA_MARKERS = ("captcha", "i'm not a robot", "verify you are human", "security check")


class BrowserBlockedError(Exception):
    """The page requires interaction we refuse to automate (login/CAPTCHA)."""


class PermissionBlockedError(Exception):
    """The tool is not registered; it was never attempted."""


@dataclass
class BrowserOutcome:
    tool: str
    ok: bool
    note: str
    detail: dict = field(default_factory=dict)
    blocked: str | None = None  # 'login' | 'captcha' | None
    error: str | None = None


class BrowserAgent:
    def __init__(
        self,
        driver: BrowserDriver | None = None,
        element_resolver: Callable[[str], Element] | None = None,
    ) -> None:
        self.driver = driver or FakeDriver()
        self._resolve = element_resolver or default_resolve_element
        self._executions: list[str] = []

    # ── navigation / reading (observe) ───────────────────────────────────
    def navigate(self, url: str) -> BrowserOutcome:
        if not url or not url.startswith(("http://", "https://")):
            return BrowserOutcome(tool="browser.navigate", ok=False, note="invalid URL")
        try:
            snap = self.driver.navigate(url)
        except Exception as exc:  # noqa: BLE001
            return BrowserOutcome(
                tool="browser.navigate", ok=False, note="navigation failed", error=str(exc)
            )
        self._executions.append("browser.navigate")
        wall = self._check_wall(snap)
        if wall:
            return self._surfaced("browser.navigate", wall, snap.url)
        return BrowserOutcome(
            tool="browser.navigate",
            ok=True,
            note=f"navigated to {url}",
            detail={"url": snap.url, "title": snap.title},
        )

    def read_page(self) -> BrowserOutcome:
        try:
            snap = self.driver.read_page()
        except Exception as exc:  # noqa: BLE001
            return BrowserOutcome(
                tool="browser.read_page", ok=False, note="could not read page", error=str(exc)
            )
        self._executions.append("browser.read_page")
        wall = self._check_wall(snap)
        if wall:
            return self._surfaced("browser.read_page", wall, snap.url)
        return BrowserOutcome(
            tool="browser.read_page",
            ok=True,
            note="read page",
            detail={"url": snap.url, "text": snap.text.strip()},
        )

    # ── form fill (prepare) ──────────────────────────────────────────────
    def fill_field(self, description: str, value: str) -> BrowserOutcome:
        try:
            element = self._resolve(description)
            self.driver.fill(element, value)
        except Exception as exc:  # noqa: BLE001
            return BrowserOutcome(
                tool="browser.fill_field", ok=False, note="fill failed", error=str(exc)
            )
        self._executions.append("browser.fill_field")
        return BrowserOutcome(
            tool="browser.fill_field",
            ok=True,
            note="field staged (not submitted)",
            detail={"element": description, "value": value},
        )

    # ── click (execute — approval gate enforced upstream) ────────────────
    def click_element(self, description: str) -> BrowserOutcome:
        try:
            snap = self.driver.read_page()
            wall = self._check_wall(snap)
            if wall:
                return self._surfaced("browser.click_element", wall, snap.url)
            element = self._resolve(description)
            after = self.driver.click(element)
        except Exception as exc:  # noqa: BLE001
            return BrowserOutcome(
                tool="browser.click_element", ok=False, note="click failed", error=str(exc)
            )
        self._executions.append("browser.click_element")
        return BrowserOutcome(
            tool="browser.click_element",
            ok=True,
            note="clicked element",
            detail={"element": description, "url_after": after.url},
        )

    # ── walls ────────────────────────────────────────────────────────────
    def _check_wall(self, snap) -> str | None:
        low = (snap.text or "").lower()
        if any(k in low for k in CAPTCHA_MARKERS):
            return "captcha"
        if any(k in low for k in LOGIN_MARKERS):
            return "login"
        return None

    def _surfaced(self, tool: str, wall: str, url: str) -> BrowserOutcome:
        """Never bypass: tell the user what stands in the way and STOP."""
        self._executions.append(f"{tool}:blocked:{wall}")
        return BrowserOutcome(
            tool=tool,
            ok=False,
            note=f"stopped: {wall} wall detected on {url} — refusing to automate",
            detail={"wall": wall, "url": url},
            blocked=wall,
        )

    # ── registry gate ────────────────────────────────────────────────────
    def classify_with_gate(self, tool: str):
        from packages.shared.permission_engine import PermissionEngine

        try:
            return PermissionEngine.classify(tool)
        except UnknownToolError as exc:
            raise PermissionBlockedError(str(exc)) from exc

    @property
    def executions(self) -> list[str]:
        return list(self._executions)


def default_resolve_element(description: str) -> Element:
    """Map a plain-language description to an Element using conventions.

    "the search box" -> by name/placeholder 'search'
    "the email field" -> by name 'email'
    "the submit button" -> by role button, name submit
    Falls back to a best-effort selector.
    """
    d = description.strip().lower()
    if d.endswith("button") or "submit" in d:
        name = d.replace("button", "").replace("the", "").strip() or "submit"
        return Element(selector=f"button:has-text('{name}')", role="button", name=name)
    if "box" in d or "field" in d or "input" in d:
        name = d.replace("the", "").replace("box", "").replace("field", "").replace("input", "").strip()
        return Element(selector=f"input[name='{name}']", role="textbox", name=name)
    return Element(selector=description, name=description)