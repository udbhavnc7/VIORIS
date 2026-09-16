"""
Computer agent — screen capture and text extraction (Phase 3, Prompt 3.1).

Screenshot and OCR are injectable so the agent is testable offline. The
default screenshot backend uses PowerShell on Windows; OCR uses an explicitly
configured script/repo path. If no OCR backend is available the agent reports
a structured failure ("no OCR backend configured") — never a phantom success.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class VisionError(Exception):
    """Screenshot or text extraction could not produce output."""


def default_screenshot(where: Path) -> Path:
    """Capture the primary screen to `where` *(a .png file), return its path."""
    where = Path(where)
    if sys.platform != "win32":
        raise VisionError("screenshot backend not implemented on this platform")
    where.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save("{0}")
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script.format(where)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not where.exists():
        raise VisionError(f"screenshot backend failed: {result.stderr.strip()[:200]}")
    return where


def default_ocr(image: Path, **_) -> str:
    """Extract visible text from an image.

    Tries tesseract via the `pytesseract` binding if present; otherwise raises
    VisionError so the caller reports the failure instead of guessing.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise VisionError(
            "no OCR backend configured (pytesseract+Pillow not installed)"
        ) from exc
    try:
        return pytesseract.image_to_string(Image.open(image), lang="eng")
    except Exception as exc:
        raise VisionError(f"OCR failed: {exc}") from exc