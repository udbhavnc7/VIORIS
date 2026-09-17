"""Tests for the VLM tool (Phase 7.3)."""

import pytest
from unittest.mock import patch, MagicMock

from packages.shared.vision_model import (
    VisionLanguageModel,
    ScreenDescription,
    ScreenElement,
)


class TestScreenDescription:
    def test_to_dict(self):
        desc = ScreenDescription(
            description="A browser window",
            elements=[
                ScreenElement(label="button", text="Submit", position="center", actionable=True)
            ],
            reasoning="The user asked about buttons",
            model="llava:7b",
        )
        d = desc.to_dict()
        assert d["description"] == "A browser window"
        assert len(d["elements"]) == 1
        assert d["elements"][0]["label"] == "button"
        assert d["elements"][0]["actionable"] is True
        assert d["reasoning"] == "The user asked about buttons"
        assert d["model"] == "llava:7b"

    def test_empty_elements(self):
        desc = ScreenDescription(description="Nothing notable")
        d = desc.to_dict()
        assert d["elements"] == []


class TestParseResponse:
    def test_valid_json_response(self):
        vlm = VisionLanguageModel()
        content = '''Here is the analysis:
        {
            "description": "An error dialog is shown",
            "elements": [
                {"label": "error dialog", "text": "File not found", "position": "center", "actionable": false},
                {"label": "OK button", "text": "OK", "position": "bottom-right", "actionable": true}
            ],
            "reasoning": "The dialog appears after a failed file open operation"
        }
        '''
        result = vlm._parse_response(content)
        assert result.description == "An error dialog is shown"
        assert len(result.elements) == 2
        assert result.elements[0].label == "error dialog"
        assert result.elements[0].text == "File not found"
        assert result.elements[0].actionable is False
        assert result.elements[1].label == "OK button"
        assert result.elements[1].actionable is True
        assert result.reasoning is not None

    def test_plain_text_response(self):
        vlm = VisionLanguageModel()
        result = vlm._parse_response("I see a desktop with a browser window open")
        assert result.description == "I see a desktop with a browser window open"
        assert result.elements == []

    def test_empty_response(self):
        vlm = VisionLanguageModel()
        result = vlm._parse_response("")
        assert result.description == "No description available"

    def test_malformed_json_falls_back(self):
        vlm = VisionLanguageModel()
        result = vlm._parse_response("Here's what I see: {not valid json")
        assert "not valid json" in result.description

    def test_json_with_extra_text(self):
        vlm = VisionLanguageModel()
        content = '''Based on the screenshot, I can see:
        {"description": "A terminal window", "elements": [], "reasoning": "Just a terminal"}
        That's all I see.'''
        result = vlm._parse_response(content)
        assert result.description == "A terminal window"


class TestDescribeScreen:
    def test_empty_screenshot(self):
        vlm = VisionLanguageModel()
        result = vlm.describe_screen(b"")
        assert "Empty" in result.description

    @patch("httpx.Client")
    def test_successful_request(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "content": '{"description": "A browser", "elements": [{"label": "tab", "text": "GitHub"}], "reasoning": null}'
            }
        }

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        vlm = VisionLanguageModel()
        result = vlm.describe_screen(b"\x89PNG fake image data")

        assert result.description == "A browser"
        assert len(result.elements) == 1
        assert result.elements[0].label == "tab"
        assert result.model == "llava:7b"

    @patch("httpx.Client")
    def test_network_error(self, mock_client_cls):
        import httpx

        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        vlm = VisionLanguageModel()
        result = vlm.describe_screen(b"\x89PNG fake image data")

        assert "failed" in result.description.lower() or "error" in result.description.lower()


class TestIsAvailable:
    @patch("httpx.Client")
    def test_available(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        vlm = VisionLanguageModel()
        assert vlm.is_available() is True

    @patch("httpx.Client")
    def test_not_available(self, mock_client_cls):
        import httpx

        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        vlm = VisionLanguageModel()
        assert vlm.is_available() is False
