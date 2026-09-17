"""Tests for Phase 10 — Contacts, Email, Browser."""

import json
import os
import tempfile

import pytest
from packages.shared.contacts import Contact, ContactBook, ContactResolver
from packages.shared.email_connector import DraftEmail, EmailMessage, GmailConnector
from packages.shared.browser import BrowserAction, BrowserManager, BrowserResult, BrowserSession


# --- Contact Tests ---


class TestContact:
    def test_exact_match(self):
        c = Contact(name="Disha", phone="+1234567890", email="disha@test.com")
        assert c.matches("Disha") == 1.0

    def test_alias_match(self):
        c = Contact(name="Disha", aliases=["Di", "Dishu"])
        assert c.matches("Di") == 0.95
        assert c.matches("Dishu") == 0.95

    def test_partial_match(self):
        c = Contact(name="Disha Kumar")
        assert c.matches("disha") == 0.8

    def test_no_match(self):
        c = Contact(name="Disha")
        assert c.matches("Random") == 0.0

    def test_case_insensitive(self):
        c = Contact(name="Disha")
        assert c.matches("disha") == 1.0
        assert c.matches("DISHA") == 1.0

    def test_to_dict(self):
        c = Contact(name="Disha", phone="123", email="d@test.com", aliases=["Di"])
        d = c.to_dict()
        assert d["name"] == "Disha"
        assert d["phone"] == "123"
        assert "Di" in d["aliases"]


class TestContactBook:
    def test_add_and_count(self):
        book = ContactBook()
        book.add(Contact(name="Disha"))
        book.add(Contact(name="Boss"))
        assert book.count() == 2

    def test_find_best_match(self):
        book = ContactBook()
        book.add(Contact(name="Disha", phone="123"))
        book.add(Contact(name="Boss", phone="456"))
        best = book.best_match("disha")
        assert best is not None
        assert best.name == "Disha"

    def test_find_returns_ranked(self):
        book = ContactBook()
        book.add(Contact(name="Disha", priority=1))
        book.add(Contact(name="Disha Kumar", priority=10))
        results = book.find("disha")
        assert len(results) >= 1

    def test_remove(self):
        book = ContactBook()
        book.add(Contact(name="Disha"))
        assert book.remove("disha")
        assert book.count() == 0

    def test_remove_not_found(self):
        book = ContactBook()
        assert not book.remove("nobody")

    def test_resolve_target(self):
        book = ContactBook()
        book.add(Contact(name="Disha", phone="123", email="d@test.com"))
        result = book.resolve_target("Disha")
        assert result["resolved"]
        assert result["name"] == "Disha"
        assert "phone" in result["channels"]
        assert "email" in result["channels"]

    def test_resolve_target_not_found(self):
        book = ContactBook()
        result = book.resolve_target("Nobody")
        assert not result["resolved"]

    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name

        try:
            book = ContactBook()
            book.add(Contact(name="Disha", phone="123", email="d@test.com", aliases=["Di"]))
            book.save(path)

            book2 = ContactBook()
            book2.load(path)
            assert book2.count() == 1
            assert book2.best_match("Disha").phone == "123"
        finally:
            os.unlink(path)

    def test_load_nonexistent(self):
        book = ContactBook()
        book.load("nonexistent.json")
        assert book.count() == 0

    def test_list_all(self):
        book = ContactBook()
        book.add(Contact(name="A"))
        book.add(Contact(name="B"))
        assert len(book.list_all()) == 2


class TestContactResolver:
    def test_resolve(self):
        book = ContactBook()
        book.add(Contact(name="Disha", phone="123"))
        resolver = ContactResolver(book)
        result = resolver.resolve("Disha")
        assert result["resolved"]

    def test_resolve_all(self):
        book = ContactBook()
        book.add(Contact(name="Disha", phone="123"))
        book.add(Contact(name="Boss", email="boss@test.com"))
        resolver = ContactResolver(book)
        results = resolver.resolve_all(["Disha", "Boss"])
        assert len(results) == 2
        assert results[0]["resolved"]
        assert results[1]["resolved"]

    def test_suggest_contacts(self):
        book = ContactBook()
        book.add(Contact(name="Disha Kumar"))
        book.add(Contact(name="Dishani"))
        book.add(Contact(name="Boss"))
        resolver = ContactResolver(book)
        suggestions = resolver.suggest_contacts("Dis")
        assert len(suggestions) >= 1


# --- Email Connector Tests ---


class TestEmailMessage:
    def test_to_dict(self):
        msg = EmailMessage(id="1", from_addr="a@b.com", subject="Test", body="Hello")
        d = msg.to_dict()
        assert d["id"] == "1"
        assert d["from"] == "a@b.com"

    def test_defaults(self):
        msg = EmailMessage()
        assert not msg.is_read
        assert not msg.has_attachments
        assert msg.labels == []


class TestDraftEmail:
    def test_to_mime(self):
        draft = DraftEmail(to="d@test.com", subject="Re: Hello", body="Hi there")
        mime = draft.to_mime()
        assert mime["to"] == "d@test.com"
        assert mime["subject"] == "Re: Hello"

    def test_with_cc(self):
        draft = DraftEmail(to="a@test.com", subject="Test", body="Body", cc="b@test.com")
        mime = draft.to_mime()
        assert mime["cc"] == "b@test.com"


class TestGmailConnector:
    def test_init(self):
        conn = GmailConnector()
        assert not conn.is_connected()

    def test_connect_no_credentials(self):
        conn = GmailConnector(credentials_path="nonexistent.json")
        success, msg = conn.connect()
        assert not success
        assert "not found" in msg

    def test_list_messages_not_connected(self):
        conn = GmailConnector()
        msgs = conn.list_messages()
        assert msgs == []

    def test_get_message_not_connected(self):
        conn = GmailConnector()
        msg = conn.get_message("123")
        assert msg is None

    def test_send_not_connected(self):
        conn = GmailConnector()
        draft = DraftEmail(to="a@test.com", subject="Test", body="Body")
        success, msg = conn.send_message(draft)
        assert not success
        assert "Not connected" in msg

    def test_mark_read_not_connected(self):
        conn = GmailConnector()
        assert not conn.mark_read("123")

    def test_get_unread_not_connected(self):
        conn = GmailConnector()
        assert conn.get_unread_count() == 0


# --- Browser Tests ---


class TestBrowserAction:
    def test_to_dict(self):
        action = BrowserAction(action="navigate", url="https://example.com")
        d = action.to_dict()
        assert d["action"] == "navigate"
        assert d["url"] == "https://example.com"

    def test_defaults(self):
        action = BrowserAction(action="click")
        assert action.timeout == 30000
        assert action.selector == ""


class TestBrowserResult:
    def test_to_dict(self):
        result = BrowserResult(success=True, action="navigate", data={"title": "Test"})
        d = result.to_dict()
        assert d["success"]
        assert d["action"] == "navigate"

    def test_with_error(self):
        result = BrowserResult(success=False, action="click", error="Selector not found")
        assert not result.success
        assert "not found" in result.error

    def test_with_screenshot(self):
        result = BrowserResult(success=True, action="screenshot", screenshot=b"\x89PNG")
        d = result.to_dict()
        assert d["has_screenshot"]


class TestBrowserSession:
    def test_init(self):
        session = BrowserSession(headless=True)
        assert not session.is_connected()

    @pytest.mark.asyncio
    async def test_start_without_playwright(self):
        session = BrowserSession()
        success, msg = await session.start()
        if not success:
            assert "not installed" in msg.lower() or "error" in msg.lower()

    @pytest.mark.asyncio
    async def test_execute_not_connected(self):
        session = BrowserSession()
        action = BrowserAction(action="navigate", url="https://example.com")
        result = await session.execute(action)
        assert not result.success
        assert "Not connected" in result.error

    @pytest.mark.asyncio
    async def test_stop(self):
        session = BrowserSession()
        await session.stop()
        assert not session.is_connected()


class TestBrowserManager:
    def test_init(self):
        manager = BrowserManager()
        assert manager.list_sessions() == []

    def test_get_session_not_found(self):
        manager = BrowserManager()
        assert manager.get_session("nonexistent") is None

    @pytest.mark.asyncio
    async def test_close_session(self):
        manager = BrowserManager()
        await manager.close_session("nonexistent")
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_close_all(self):
        manager = BrowserManager()
        await manager.close_all()
        assert manager.list_sessions() == []
