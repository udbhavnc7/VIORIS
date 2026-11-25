"""TTS services moved to `packages/shared` (used by the gateway). Re-export."""

from packages.shared.tts import AudioPlayer, PiperTTS, TTSUnavailableError, find_piper_voice

__all__ = ["AudioPlayer", "PiperTTS", "TTSUnavailableError", "find_piper_voice"]
