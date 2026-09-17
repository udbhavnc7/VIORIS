"""Flutter UI Models — data structures for the mobile app."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ScreenType(Enum):
    CALL = "call"
    APPROVAL = "approval"
    SETTINGS = "settings"
    CONTACTS = "contacts"
    HISTORY = "history"
    DASHBOARD = "dashboard"


class CallScreenMode(Enum):
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    DIGEST = "digest"


@dataclass
class CallScreenModel:
    mode: CallScreenMode = CallScreenMode.RINGING
    caller_name: str = ""
    caller_id: str = ""
    duration_seconds: int = 0
    is_muted: bool = False
    is_speaker: bool = False
    trust_level: str = "unverified"
    audio_level: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "caller_name": self.caller_name,
            "duration_seconds": self.duration_seconds,
            "is_muted": self.is_muted,
            "is_speaker": self.is_speaker,
            "trust_level": self.trust_level,
            "audio_level": self.audio_level,
        }


@dataclass
class ApprovalCardModel:
    action_id: str
    tool: str
    description: str
    diff_summary: str = ""
    risk_level: str = "observe"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    expires_in_seconds: int = 30

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "tool": self.tool,
            "description": self.description,
            "diff_summary": self.diff_summary,
            "risk_level": self.risk_level,
            "created_at": self.created_at,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass
class SettingsModel:
    wake_word_enabled: bool = True
    wake_word_phrase: str = "Hey Vioris"
    voice_output_enabled: bool = True
    auto_approve_observe: bool = True
    auto_approve_prepare: bool = False
    call_timeout_seconds: int = 30
    digest_hours: list[int] = field(default_factory=lambda: [8, 13, 18])
    theme: str = "dark"
    language: str = "en"

    def to_dict(self) -> dict:
        return {
            "wake_word_enabled": self.wake_word_enabled,
            "wake_word_phrase": self.wake_word_phrase,
            "voice_output_enabled": self.voice_output_enabled,
            "auto_approve_observe": self.auto_approve_observe,
            "auto_approve_prepare": self.auto_approve_prepare,
            "call_timeout_seconds": self.call_timeout_seconds,
            "digest_hours": self.digest_hours,
            "theme": self.theme,
            "language": self.language,
        }

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)


@dataclass
class ContactModel:
    name: str
    phone: str = ""
    email: str = ""
    avatar_url: str = ""
    relationship: str = ""
    last_contact: str = ""
    is_favorite: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "avatar_url": self.avatar_url,
            "relationship": self.relationship,
            "last_contact": self.last_contact,
            "is_favorite": self.is_favorite,
        }


@dataclass
class HistoryItem:
    timestamp: str
    action: str
    tool: str
    result: str = ""
    success: bool = True
    duration_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "tool": self.tool,
            "result": self.result,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DashboardStats:
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    active_calls: int = 0
    pending_approvals: int = 0
    contacts_count: int = 0
    uptime_seconds: int = 0

    def to_dict(self) -> dict:
        return {
            "total_actions": self.total_actions,
            "successful_actions": self.successful_actions,
            "failed_actions": self.failed_actions,
            "active_calls": self.active_calls,
            "pending_approvals": self.pending_approvals,
            "contacts_count": self.contacts_count,
            "uptime_seconds": self.uptime_seconds,
        }


@dataclass
class UIEvent:
    event_type: str
    payload: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_json(self) -> str:
        return json.dumps({
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, raw: str) -> "UIEvent":
        d = json.loads(raw)
        return cls(event_type=d["event_type"], payload=d.get("payload", {}))
