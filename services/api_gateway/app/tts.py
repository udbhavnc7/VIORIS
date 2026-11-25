"""Piper TTS for pushing voice responses to the phone (10b). Lazy-loaded."""

from __future__ import annotations

import os
from pathlib import Path

_tts = None


def get_tts():
    global _tts
    if _tts is None:
        from packages.shared.tts import PiperTTS, find_piper_voice

        voice_name = os.getenv("VIORIS_TTS_VOICE", "en_US-lessac-medium")
        voice = find_piper_voice(
            voice_name,
            [Path("models"), Path("vioris_data/models"), Path("apps/desktop_agent/models")],
        )
        _tts = PiperTTS(voice)
    return _tts
