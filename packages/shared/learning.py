"""Learning & Personalization — user preferences, adaptive responses."""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class UserPreference:
    key: str
    value: Any
    category: str = "general"
    confidence: float = 1.0
    source: str = "explicit"  # explicit, inferred, default
    last_updated: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "confidence": self.confidence,
            "source": self.source,
            "last_updated": self.last_updated,
        }


@dataclass
class LearnedPattern:
    pattern_id: str
    trigger: str
    response: str
    frequency: int = 1
    confidence: float = 0.5
    last_seen: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "trigger": self.trigger,
            "response": self.response,
            "frequency": self.frequency,
            "confidence": self.confidence,
            "last_seen": self.last_seen,
        }


class PreferenceStore:
    """Stores and retrieves user preferences."""

    def __init__(self, persist_path: Optional[str] = None):
        self._preferences: dict[str, UserPreference] = {}
        self._persist_path = persist_path
        if persist_path:
            self._load()

    def set(self, key: str, value: Any, category: str = "general", source: str = "explicit"):
        existing = self._preferences.get(key)
        confidence = 1.0 if source == "explicit" else 0.8

        if existing:
            existing.value = value
            existing.confidence = confidence
            existing.source = source
            existing.last_updated = datetime.now(UTC).isoformat()
        else:
            self._preferences[key] = UserPreference(
                key=key, value=value, category=category, confidence=confidence, source=source,
            )

        if self._persist_path:
            self._save()

    def get(self, key: str, default: Any = None) -> Any:
        pref = self._preferences.get(key)
        return pref.value if pref else default

    def get_preference(self, key: str) -> Optional[UserPreference]:
        return self._preferences.get(key)

    def remove(self, key: str) -> bool:
        if key in self._preferences:
            del self._preferences[key]
            if self._persist_path:
                self._save()
            return True
        return False

    def list_all(self) -> list[UserPreference]:
        return list(self._preferences.values())

    def list_by_category(self, category: str) -> list[UserPreference]:
        return [p for p in self._preferences.values() if p.category == category]

    def export(self) -> dict:
        return {k: v.to_dict() for k, v in self._preferences.items()}

    def import_prefs(self, data: dict):
        for key, pref_data in data.items():
            self._preferences[key] = UserPreference(**pref_data)

    def _save(self):
        if self._persist_path:
            Path(self._persist_path).write_text(json.dumps(self.export(), indent=2))

    def _load(self):
        if self._persist_path and os.path.exists(self._persist_path):
            content = Path(self._persist_path).read_text()
            if content.strip():
                data = json.loads(content)
                self.import_prefs(data)


class PatternLearner:
    """Learns and applies patterns from user interactions."""

    def __init__(self, persist_path: Optional[str] = None):
        self._patterns: dict[str, LearnedPattern] = {}
        self._persist_path = persist_path
        if persist_path:
            self._load()

    def record(self, trigger: str, response: str):
        pattern_id = f"{hash(trigger)}_{hash(response)}"
        existing = self._patterns.get(pattern_id)

        if existing:
            existing.frequency += 1
            existing.confidence = min(0.95, existing.confidence + 0.05)
            existing.last_seen = datetime.now(UTC).isoformat()
        else:
            self._patterns[pattern_id] = LearnedPattern(
                pattern_id=pattern_id,
                trigger=trigger,
                response=response,
            )

        if self._persist_path:
            self._save()

    def find_match(self, trigger: str, threshold: float = 0.5) -> Optional[LearnedPattern]:
        best_match = None
        best_score = 0.0

        for pattern in self._patterns.values():
            score = self._similarity(trigger, pattern.trigger)
            if score > best_score and score >= threshold and pattern.confidence >= 0.3:
                best_score = score
                best_match = pattern

        return best_match

    def get_suggestions(self, partial: str, limit: int = 5) -> list[LearnedPattern]:
        scored = []
        for pattern in self._patterns.values():
            score = self._similarity(partial, pattern.trigger)
            if score > 0.3:
                scored.append((score, pattern))
        scored.sort(key=lambda x: (-x[0], -x[1].frequency))
        return [p for _, p in scored[:limit]]

    def prune(self, min_frequency: int = 2, min_confidence: float = 0.3) -> int:
        to_remove = [
            pid for pid, p in self._patterns.items()
            if p.frequency < min_frequency and p.confidence < min_confidence
        ]
        for pid in to_remove:
            del self._patterns[pid]
        return len(to_remove)

    def list_all(self) -> list[LearnedPattern]:
        return list(self._patterns.values())

    def _similarity(self, a: str, b: str) -> float:
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return 0.0
        intersection = a_words & b_words
        union = a_words | b_words
        return len(intersection) / len(union) if union else 0.0

    def _save(self):
        if self._persist_path:
            data = {k: v.to_dict() for k, v in self._patterns.items()}
            Path(self._persist_path).write_text(json.dumps(data, indent=2))

    def _load(self):
        if self._persist_path and os.path.exists(self._persist_path):
            content = Path(self._persist_path).read_text()
            if content.strip():
                data = json.loads(content)
                for key, pattern_data in data.items():
                    self._patterns[key] = LearnedPattern(**pattern_data)


class AdaptiveResponder:
    """Adapts responses based on user preferences and learned patterns."""

    def __init__(self, preferences: PreferenceStore, patterns: PatternLearner):
        self.preferences = preferences
        self.patterns = patterns

    def get_response_style(self) -> str:
        return self.preferences.get("response_style", "concise")

    def get_greeting(self) -> str:
        name = self.preferences.get("user_name", "")
        style = self.get_response_style()
        if style == "formal":
            return f"Good {self._time_of_day()}, {name}." if name else f"Good {self._time_of_day()}."
        elif style == "casual":
            return f"Hey {name}!" if name else "Hey!"
        return f"Hi {name}." if name else "Hi."

    def adapt_response(self, base_response: str, context: Optional[dict] = None) -> str:
        style = self.get_response_style()
        if style == "verbose":
            return f"Sure! {base_response} Let me know if you need anything else."
        elif style == "minimal":
            words = base_response.split()
            if len(words) > 10:
                return " ".join(words[:10]) + "..."
        return base_response

    def should_auto_approve(self, risk_level: str) -> bool:
        auto_approve = self.preferences.get("auto_approve_observe", True)
        if risk_level == "observe":
            return auto_approve
        elif risk_level == "prepare":
            return self.preferences.get("auto_approve_prepare", False)
        return False

    def _time_of_day(self) -> str:
        hour = datetime.now(UTC).hour
        if hour < 12:
            return "morning"
        elif hour < 17:
            return "afternoon"
        return "evening"
