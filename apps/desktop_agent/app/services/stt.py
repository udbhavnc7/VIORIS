"""Speech-to-text services moved to `packages/shared` (used by the gateway). Re-export."""

from packages.shared.stt import WhisperSTT, save_wav

__all__ = ["WhisperSTT", "save_wav"]
