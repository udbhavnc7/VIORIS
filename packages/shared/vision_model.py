"""
Vision-Language Model Tool (Phase 7.3).

Local VLM integration for screen understanding — goes beyond OCR by providing
actual reasoning about what's on screen ("the dialog says the download failed,
should I retry?"). Uses Ollama-hosted vision models (LLaVA, Moondream,
Qwen2-VL, etc.) to describe and reason about screenshots.

Registered as an Observe-tier tool — never acts, only observes and reports.

Usage:
    vlm = VisionLanguageModel()
    result = vlm.describe_screen(screenshot_bytes, "What does this error say?")
    # -> ScreenDescription(description="...", elements=[...], reasoning="...")
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default Ollama vision model — LLaVA is the most widely available
DEFAULT_VLM_MODEL = "llava:7b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"


@dataclass
class ScreenElement:
    """A notable element detected on screen."""

    label: str  # e.g. "error dialog", "download button", "search bar"
    text: str | None = None  # any visible text on this element
    position: str | None = None  # relative position: "top-left", "center", etc.
    actionable: bool = False  # whether this element looks clickable/interactive


@dataclass
class ScreenDescription:
    """Result from a VLM screen analysis."""

    description: str  # natural language description of what's on screen
    elements: list[ScreenElement] = field(default_factory=list)
    reasoning: str | None = None  # if the VLM was asked a question, its reasoning
    raw_response: str | None = None  # full model output for debugging
    model: str | None = None

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "elements": [
                {
                    "label": e.label,
                    "text": e.text,
                    "position": e.position,
                    "actionable": e.actionable,
                }
                for e in self.elements
            ],
            "reasoning": self.reasoning,
            "model": self.model,
        }


class VisionLanguageModel:
    """Local VLM via Ollama for screen understanding.

    Uses Ollama's multimodal API endpoint to send images (screenshots) to a
    vision-language model and get structured descriptions back.

    The model must support the `api/chat` endpoint with image input. Popular
    options: llava, moondream, qwen2-vl, bakllava.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model or DEFAULT_VLM_MODEL
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def describe_screen(
        self,
        screenshot_bytes: bytes,
        question: str | None = None,
        format: str = "png",
    ) -> ScreenDescription:
        """Analyze a screenshot and optionally answer a question about it.

        Args:
            screenshot_bytes: Raw image bytes (PNG, JPEG, etc.)
            question: Optional question about the screen content.
                      If None, gives a general description.
            format: Image format for the API (png, jpeg).

        Returns:
            ScreenDescription with the model's analysis.
        """
        if not screenshot_bytes:
            return ScreenDescription(description="Empty screenshot provided")

        # Encode image to base64 for Ollama API
        img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        # Build the prompt
        if question:
            prompt = (
                f"Analyze this screenshot carefully. {question}\n\n"
                "Respond with a JSON object containing:\n"
                '- "description": a brief description of what you see\n'
                '- "elements": a list of notable UI elements, each with "label", '
                '"text" (if any visible text), "position", and "actionable" (boolean)\n'
                '- "reasoning": your step-by-step reasoning to answer the question\n'
            )
        else:
            prompt = (
                "Describe this screenshot in detail. What applications, windows, "
                "dialogs, buttons, and text are visible?\n\n"
                "Respond with a JSON object containing:\n"
                '- "description": a detailed description of what you see\n'
                '- "elements": a list of notable UI elements, each with "label", '
                '"text" (if any visible text), "position", and "actionable" (boolean)\n'
            )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [img_b64],
                }
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("VLM request failed: %s", exc)
            return ScreenDescription(
                description=f"VLM request failed: {exc}",
                raw_response=str(exc),
            )
        except Exception as exc:
            logger.warning("VLM unexpected error: %s", exc)
            return ScreenDescription(
                description=f"VLM error: {exc}",
                raw_response=str(exc),
            )

        # Parse the response
        content = (data.get("message") or {}).get("content", "")
        return self._parse_response(content)

    def _parse_response(self, content: str) -> ScreenDescription:
        """Parse the VLM's response into a structured ScreenDescription."""
        import json

        # Try to extract JSON from the response
        parsed = None
        # Look for JSON block in the response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            try:
                parsed = json.loads(content[start:end])
            except json.JSONDecodeError:
                pass

        if not parsed:
            # Fallback: treat entire response as description
            return ScreenDescription(
                description=content.strip() or "No description available",
                raw_response=content,
                model=self.model,
            )

        # Build elements list
        elements = []
        for e in parsed.get("elements", []):
            if isinstance(e, dict):
                elements.append(
                    ScreenElement(
                        label=e.get("label", "unknown"),
                        text=e.get("text"),
                        position=e.get("position"),
                        actionable=e.get("actionable", False),
                    )
                )

        return ScreenDescription(
            description=parsed.get("description", content.strip()),
            elements=elements,
            reasoning=parsed.get("reasoning"),
            raw_response=content,
            model=self.model,
        )

    def is_available(self) -> bool:
        """Check if the VLM backend is reachable."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False
