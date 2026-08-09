"""
Desktop agent configuration (Phase 1).

Values come from environment variables (see .env.example). The wake-word model
name is the openWakeWord pretrained key ('hey_jarvis' ships built-in; a custom
'vioris' model can be substituted once trained). All audio paths are local.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AgentConfig:
    # Wake word
    wake_word_model: str = os.getenv("WAKE_WORD_MODEL", "hey_jarvis")
    wake_phrase: str = os.getenv("WAKE_PHRASE", "vioris")
    wake_threshold: float = float(os.getenv("WAKE_THRESHOLD", "0.5"))
    # Clap pattern
    clap_enabled: bool = _env_bool("CLAP_WAKE_ENABLED", True)
    claps_required: int = int(os.getenv("CLAPS_REQUIRED", "2"))
    clap_window_seconds: float = float(os.getenv("CLAP_WINDOW_SECONDS", "2.5"))
    # STT
    stt_model: str = os.getenv("STT_MODEL", "base")
    stt_device: str = os.getenv("STT_DEVICE", "cpu")
    stt_compute_type: str = os.getenv("STT_COMPUTE_TYPE", "int8")
    # TTS
    tts_model: str = os.getenv("TTS_MODEL", "")
    tts_sample_rate: int = int(os.getenv("TTS_SAMPLE_RATE", "22050"))
    # Audio
    sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    chunk_ms: int = int(os.getenv("AUDIO_CHUNK_MS", "80"))
    capture_timeout_seconds: float = float(os.getenv("CAPTURE_TIMEOUT_SECONDS", "10.0"))
    # Interrupt: stop must cancel within this window
    stop_latency_ms: int = int(os.getenv("STOP_LATENCY_MS", "500"))

    models_dir: Path = field(default_factory=lambda: Path(os.getenv("MODELS_DIR", "models")))
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("DB_PATH", "vioris_data/vioris.db"))
    )

    @property
    def chunk_samples(self) -> int:
        return int(self.sample_rate * self.chunk_ms / 1000)

    def tts_model_path(self) -> Path:
        """Prefer an explicit env path; else look in models/. Returns '' if unset."""
        if self.tts_model:
            return Path(self.tts_model)
        return Path("")
