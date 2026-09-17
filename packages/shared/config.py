"""Configuration — env vars, defaults, validation."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ViorisConfig:
    """Central configuration for all Vioris services."""

    # LLM
    llm_provider: str = "ollama"
    llm_model: str = "llama3.2"
    ollama_url: str = "http://localhost:11434"
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_api_key: str = ""

    # STT / TTS
    stt_engine: str = "whisper"
    stt_model: str = "base"
    tts_engine: str = "piper"
    tts_voice: str = "en_US-lessac-medium"
    whisper_device: str = "cpu"

    # Wake word
    wake_word_enabled: bool = True
    wake_word_phrase: str = "hey vioris"
    wake_word_model: str = "openwakeword"

    # Database
    db_url: str = "postgresql://localhost:5432/vioris"
    redis_url: str = "redis://localhost:6379/0"

    # Smart Home
    ha_url: str = "http://localhost:8123"
    ha_token: str = ""

    # Remote Access
    tailscale_ip: str = ""
    ws_port: int = 8765

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Security
    permission_engine_strict: bool = True
    kill_phrase_enabled: bool = True
    voiceprint_enabled: bool = False
    max_undo_window_seconds: int = 300

    # Contacts
    contacts_file: str = "data/contacts.json"

    # Paths
    data_dir: str = "data"
    log_dir: str = "logs"
    credentials_dir: str = "credentials"

    @classmethod
    def from_env(cls) -> "ViorisConfig":
        return cls(
            llm_provider=os.getenv("VIORIS_LLM_PROVIDER", "ollama"),
            llm_model=os.getenv("VIORIS_LLM_MODEL", "llama3.2"),
            ollama_url=os.getenv("VIORIS_OLLAMA_URL", "http://localhost:11434"),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            stt_engine=os.getenv("VIORIS_STT_ENGINE", "whisper"),
            stt_model=os.getenv("VIORIS_STT_MODEL", "base"),
            tts_engine=os.getenv("VIORIS_TTS_ENGINE", "piper"),
            tts_voice=os.getenv("VIORIS_TTS_VOICE", "en_US-lessac-medium"),
            whisper_device=os.getenv("VIORIS_WHISPER_DEVICE", "cpu"),
            wake_word_enabled=os.getenv("VIORIS_WAKE_WORD_ENABLED", "true").lower() == "true",
            wake_word_phrase=os.getenv("VIORIS_WAKE_PHRASE", "hey vioris"),
            db_url=os.getenv("VIORIS_DB_URL", "postgresql://localhost:5432/vioris"),
            redis_url=os.getenv("VIORIS_REDIS_URL", "redis://localhost:6379/0"),
            ha_url=os.getenv("HA_URL", "http://localhost:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            tailscale_ip=os.getenv("VIORIS_TAILSCALE_IP", ""),
            ws_port=int(os.getenv("VIORIS_WS_PORT", "8765")),
            api_host=os.getenv("VIORIS_API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("VIORIS_API_PORT", "8000")),
            contacts_file=os.getenv("VIORIS_CONTACTS_FILE", "data/contacts.json"),
        )

    def validate(self) -> tuple[bool, list[str]]:
        errors = []
        if not self.ollama_url:
            errors.append("VIORIS_OLLAMA_URL is required")
        if self.ws_port < 1 or self.ws_port > 65535:
            errors.append("VIORIS_WS_PORT must be 1-65535")
        if self.api_port < 1 or self.api_port > 65535:
            errors.append("VIORIS_API_PORT must be 1-65535")
        return len(errors) == 0, errors

    def ensure_dirs(self):
        for d in [self.data_dir, self.log_dir, self.credentials_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)
