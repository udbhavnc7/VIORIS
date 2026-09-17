"""Call Experience — ring events, call state, email react, proactive digest."""

import json
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional


class CallState(Enum):
    IDLE = "idle"
    RINGING = "ringing"
    CONNECTED = "connected"
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class RingSource(Enum):
    INCOMING = "incoming"
    PROACTIVE = "proactive"
    VOICEWAKE = "voicewake"


@dataclass
class RingEvent:
    """Sent to the phone UI when Vioris initiates or receives a call."""

    call_id: str
    source: RingSource
    caller_name: str
    caller_id: str = ""
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> str:
        return json.dumps({
            "call_id": self.call_id,
            "source": self.source.value,
            "caller_name": self.caller_name,
            "caller_id": self.caller_id,
            "reason": self.reason,
            "created_at": self.created_at,
        })

    @classmethod
    def from_json(cls, data: str) -> "RingEvent":
        d = json.loads(data)
        return cls(
            call_id=d["call_id"],
            source=RingSource(d["source"]),
            caller_name=d["caller_name"],
            caller_id=d.get("caller_id", ""),
            reason=d.get("reason", ""),
            created_at=d.get("created_at", datetime.now(UTC).isoformat()),
        )


@dataclass
class AudioFrame:
    """A single audio chunk in the stream."""

    data: bytes
    timestamp: float
    sequence: int
    sample_rate: int = 16000

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "sample_rate": self.sample_rate,
        })

    def encode_audio(self) -> bytes:
        return self.data


@dataclass
class AudioStream:
    """Bidirectional audio stream over WebSocket."""

    frames: list[AudioFrame] = field(default_factory=list)
    _sequence: int = field(default=0, init=False, repr=False)

    def push(self, data: bytes, timestamp: float, sample_rate: int = 16000) -> AudioFrame:
        frame = AudioFrame(data=data, timestamp=timestamp, sequence=self._sequence, sample_rate=sample_rate)
        self.frames.append(frame)
        self._sequence += 1
        return frame

    def as_iterator(self, chunk_size: int = 1) -> AsyncIterator[AudioFrame]:
        return _frame_iterator(self.frames, chunk_size)


async def _frame_iterator(frames: list[AudioFrame], chunk_size: int) -> AsyncIterator[AudioFrame]:
    for i in range(0, len(frames), chunk_size):
        for frame in frames[i : i + chunk_size]:
            yield frame
        await asyncio.sleep(0)


@dataclass
class EmailReact:
    """React to an email (distinct from replying)."""

    REACTIONS = ("👍", "❤️", "😄", "😮", "😢", "🙏", "None")

    email_id: str
    sender: str
    subject: str
    reaction: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def validate(self) -> tuple[bool, str]:
        if not self.email_id:
            return False, "email_id required"
        if not self.sender:
            return False, "sender required"
        if self.reaction and self.reaction not in self.REACTIONS:
            return False, f"reaction must be one of: {', '.join(self.REACTIONS)}"
        return True, ""

    def to_dict(self) -> dict:
        return {
            "email_id": self.email_id,
            "sender": self.sender,
            "subject": self.subject,
            "reaction": self.reaction,
            "created_at": self.created_at,
        }


class DigestItem:
    """A single item in a proactive digest."""

    def __init__(self, item_type: str, summary: str, source: str, priority: str = "normal", actionable: bool = False):
        self.item_type = item_type
        self.summary = summary
        self.source = source
        self.priority = priority
        self.actionable = actionable

    def to_dict(self) -> dict:
        return {
            "item_type": self.item_type,
            "summary": self.summary,
            "source": self.source,
            "priority": self.priority,
            "actionable": self.actionable,
        }


class DigestSchedule:
    """Controls when proactive digests are triggered."""

    TRIGGER_HOURS = (8, 13, 18)  # 8am, 1pm, 6pm

    def __init__(self, custom_hours: Optional[list[int]] = None):
        self.trigger_hours = custom_hours or list(self.TRIGGER_HOURS)
        self.last_digest: Optional[datetime] = None

    def should_trigger(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(UTC)
        current_hour = now.hour

        if current_hour not in self.trigger_hours:
            return False

        if self.last_digest:
            since_last = (now - self.last_digest).total_seconds()
            if since_last < 3600:
                return False

        return True

    def record_trigger(self, now: Optional[datetime] = None):
        self.last_digest = now or datetime.now(UTC)

    def next_trigger(self, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or datetime.now(UTC)
        for hour in self.trigger_hours:
            next_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_time > now:
                return next_time
        tomorrow = now + timedelta(days=1)
        for hour in self.trigger_hours:
            next_time = tomorrow.replace(hour=hour, minute=0, second=0, microsecond=0)
            if next_time > now:
                return next_time
        return None


@dataclass
class ProactiveDigest:
    """A compiled digest of items to present to the user."""

    call_id: str
    items: list[DigestItem] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def compose_spoken(self) -> str:
        if not self.items:
            return "Nothing new."
        parts = []
        actionables = [i for i in self.items if i.actionable]
        non_actionables = [i for i in self.items if not i.actionable]

        for item in non_actionables:
            parts.append(f"{item.item_type}: {item.summary}")
        if actionables:
            parts.append(f"{len(actionables)} items need your attention.")
        return ". ".join(parts) + "."

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "items": [i.to_dict() for i in self.items],
            "created_at": self.created_at,
        }


class CallExperience:
    """Manages call lifecycle — ring, connect, audio, digest."""

    def __init__(self, audio_stream: Optional[AudioStream] = None):
        self.state = CallState.IDLE
        self.audio_stream = audio_stream or AudioStream()
        self.call_id: Optional[str] = None
        self.ring_event: Optional[RingEvent] = None
        self.digest = DigestSchedule()
        self._state_handlers: dict[CallState, list[Callable]] = {}

    def on_state_change(self, state: CallState, handler: Callable):
        self._state_handlers.setdefault(state, []).append(handler)

    def _set_state(self, new_state: CallState):
        old = self.state
        self.state = new_state
        for handler in self._state_handlers.get(new_state, []):
            handler(old, new_state)

    def start_ring(self, event: RingEvent):
        self.call_id = event.call_id
        self.ring_event = event
        self._set_state(CallState.RINGING)

    def accept_call(self):
        if self.state == CallState.RINGING:
            self._set_state(CallState.CONNECTED)

    def start_active(self):
        if self.state == CallState.CONNECTED:
            self._set_state(CallState.ACTIVE)

    def end_call(self):
        self._set_state(CallState.ENDED)
        self._set_state(CallState.IDLE)
        self.call_id = None
        self.ring_event = None

    def push_audio(self, data: bytes, timestamp: float, sample_rate: int = 16000) -> AudioFrame:
        return self.audio_stream.push(data, timestamp, sample_rate)

    def get_digest(self, call_id: str, items: Optional[list[DigestItem]] = None) -> ProactiveDigest:
        return ProactiveDigest(call_id=call_id, items=items or [])

    def check_digest_trigger(self, now: Optional[datetime] = None) -> bool:
        return self.digest.should_trigger(now)

    def trigger_digest(self, now: Optional[datetime] = None):
        self.digest.record_trigger(now)
