"""Computer agent unit tests: injectable backends, no live desktop needed."""

from pathlib import Path

import pytest

from agents.computer.app.agent import ComputerAgent, PermissionBlockedError
from agents.computer.app.vision import VisionError
from agents.computer.app.windows import WindowLookupError
from packages.shared.permission_engine import PermissionEngine, register_phase3_tools
from packages.shared.vision_model import ScreenDescription, ScreenElement

register_phase3_tools()


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase3_tools()
    yield
    PermissionEngine.reset()


def fake_windows() -> list[dict]:
    return [
        {"handle": 1, "title": "Visual Studio Code — test.py"},
        {"handle": 2, "title": "Slack"},
        {"handle": 3, "title": "Terminal"},
    ]


def make_agent(tmp_path, fail=None, **kw):
    kw.setdefault("list_windows_fn", fake_windows)
    kw.setdefault("screenshot_dir", tmp_path)

    def fake_screenshot(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if fail == "screenshot":
            raise VisionError("boom")
        path.write_bytes(b"\x89PNG" + b"\x00" * 32)
        return path

    def fake_ocr(path):
        if fail == "ocr":
            raise VisionError("no tesseract")
        return "hello vioris, open my last project\n"

    kw.setdefault("screenshot_fn", fake_screenshot)
    kw.setdefault("ocr_fn", fake_ocr)
    return ComputerAgent(**kw)


class TestListWindows:
    def test_lists_window_titles(self, tmp_path):
        out = make_agent(tmp_path).list_windows()
        assert out.ok
        assert out.verified
        assert out.detail["count"] == 3
        titles = [w["title"] for w in out.detail["windows"]]
        assert "Slack" in titles

    def test_backend_failure_is_typed_error(self, tmp_path):
        def boom():
            raise WindowLookupError("not on this platform")

        out = make_agent(tmp_path, list_windows_fn=boom).list_windows()
        assert not out.ok
        assert "not on this platform" in out.error


class TestOpenApp:
    def test_open_and_verify_against_screen(self, tmp_path):
        opened = []
        agent = make_agent(tmp_path, open_app_fn=opened.append)
        out = agent.open_app("Visual Studio Code")
        assert out.ok
        assert opened == ["Visual Studio Code"]
        assert out.verified  # the window title matched
        assert "Visual Studio Code — test.py" in str(out.detail["matched_windows"])

    def test_missing_window_title_reports_failure_verification(self, tmp_path):
        opened = []
        agent = make_agent(tmp_path, open_app_fn=opened.append)
        out = agent.open_app("Spotify")  # not in fake_windows
        assert out.ok  # launcher succeeded...
        assert not out.verified  # ...but the screen does not show it
        assert "no matching window title" in out.error

    def test_empty_name_is_refused(self, tmp_path):
        out = make_agent(tmp_path).open_app("   ")
        assert not out.ok
        assert "no app name" in out.note


class TestScreenCapture:
    def test_screenshot_writes_bytes(self, tmp_path):
        out = make_agent(tmp_path).capture_screenshot()
        assert out.ok
        assert out.verified
        assert out.detail["bytes"] > 0
        assert Path(out.detail["path"]).exists()

    def test_screenshot_failure_is_reported(self, tmp_path):
        out = make_agent(tmp_path, fail="screenshot").capture_screenshot()
        assert not out.ok
        assert "boom" in out.error


class TestReadScreenText:
    def test_returns_visible_text(self, tmp_path):
        out = make_agent(tmp_path).read_screen_text()
        assert out.ok
        assert out.verified
        assert "hello" in out.detail["text"].lower()

    def test_ocr_unavailable_is_failure_not_fantasy(self, tmp_path):
        out = make_agent(tmp_path, fail="ocr").read_screen_text()
        assert not out.ok
        assert "no tesseract" in out.error

    def test_observed_screen_text_is_quarantined_as_data(self, tmp_path):
        # The shared fake OCR yields "hello"; the boundary must still be present
        # so anything else on screen can never be read as a user instruction.
        out = make_agent(tmp_path).read_screen_text()
        assert "[UNTRUSTED DATA" in out.detail["text"]
        assert "<untrusted_data>" in out.detail["text"]
        assert out.detail["untrusted"]["source"] == "screen"
        assert out.detail["untrusted"]["location"] == "live screen"


class TestDescribeScreen:
    def test_local_vlm_describes_a_fresh_frame(self, tmp_path):
        calls = []

        def fake_vlm(image, question):
            calls.append((image, question))
            return ScreenDescription(
                description="An error dialog says the download failed",
                elements=[
                    ScreenElement(
                        label="error dialog", text="download failed", actionable=False
                    )
                ],
                reasoning="The dialog reports a failure; retrying would be a separate action.",
                model="llava:7b",
            )

        out = make_agent(tmp_path, vlm_describe_fn=fake_vlm).describe_screen(
            "What does the error say?"
        )
        assert out.ok and out.verified
        assert out.detail["description"] == "An error dialog says the download failed"
        assert out.detail["elements"][0]["label"] == "error dialog"
        assert out.detail["model"] == "llava:7b"
        assert calls[0][0].startswith(b"\x89PNG")  # a freshly captured frame
        assert calls[0][1] == "What does the error say?"
        # VLM output is observed data, never a user command.
        assert "[UNTRUSTED DATA" in out.detail["text"]
        assert out.detail["untrusted"]["source"] == "screen_vlm"

    def test_no_frame_means_no_vlm_call(self, tmp_path):
        calls = []

        def fake_vlm(image, question):
            calls.append(image)
            return ScreenDescription(description="impossible")

        out = make_agent(
            tmp_path, fail="screenshot", vlm_describe_fn=fake_vlm
        ).describe_screen()
        assert not out.ok
        assert "no pixels" in out.error
        assert calls == []

    def test_vlm_backend_failure_is_reported_not_fantasy(self, tmp_path):
        def fake_vlm(image, question):
            return ScreenDescription(
                description="VLM request failed: connection refused",
                raw_response="connection refused",
            )

        out = make_agent(tmp_path, vlm_describe_fn=fake_vlm).describe_screen()
        assert not out.ok
        assert not out.verified
        assert "connection refused" in out.error

    def test_vlm_exception_is_a_typed_failure(self, tmp_path):
        def boom(image, question):
            raise RuntimeError("ollama not installed")

        out = make_agent(tmp_path, vlm_describe_fn=boom).describe_screen()
        assert not out.ok
        assert "ollama not installed" in out.error
        assert out.detail == {}


class TestLockWorkstation:
    def test_lock_success_through_injected_backend(self, tmp_path):
        locked = []
        out = make_agent(tmp_path, lock_workstation_fn=lambda: locked.append(True)).lock_workstation()
        assert out.ok
        assert out.verified
        assert locked == [True]

    def test_lock_failure_is_reported(self, tmp_path):
        def boom():
            raise RuntimeError("lock refused")

        out = make_agent(tmp_path, lock_workstation_fn=boom).lock_workstation()
        assert not out.ok
        assert "lock refused" in out.error
        assert not out.verified


class TestRegistryGate:
    def test_unregistered_computer_tool_never_runs(self, tmp_path):
        agent = make_agent(tmp_path)
        with pytest.raises(PermissionBlockedError):
            agent.classify_with_gate("computer.burn_disk")