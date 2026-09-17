"""
Routine Mining (Phase 7.4).

Mines local task history for repeated action sequences and surfaces them as
Prepare-tier suggestions. The user must explicitly approve before any automation
is created — this stays at Observe/Prepare tier by construction.

Usage:
    miner = RoutineMiner(task_store)
    suggestions = miner.find_routines()
    for s in suggestions:
        print(f"You often {s.description}. Want me to automate this?")

Mining strategy:
  1. Group tasks by similar request patterns (fuzzy text matching).
  2. Identify sequences that repeat at similar times of day or day of week.
  3. Surface only patterns that appear N+ times within a configurable window.
  4. Each suggestion includes an explanation of what was observed — the user
     decides whether to automate, not the agent.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Minimum occurrences to suggest a routine
DEFAULT_MIN_OCCURRENCES = 3

# Time window for pattern detection (days)
DEFAULT_WINDOW_DAYS = 30


@dataclass
class TaskRecord:
    """Lightweight task record for mining (avoids full schema dependency)."""

    task_id: str
    request: str
    tool: str | None = None
    created_at: datetime | None = None
    status: str = "completed"


@dataclass
class RoutinePattern:
    """A detected repeated pattern in task history."""

    pattern_id: str
    description: str  # human-readable description of the pattern
    tool: str  # the tool/action that repeats
    occurrences: int  # how many times observed
    time_pattern: str | None = None  # e.g. "weekday_morning", "daily_9am"
    sample_requests: list[str] = field(default_factory=list)  # example utterances
    confidence: float = 0.0  # 0.0–1.0, how confident we are this is a real routine

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "tool": self.tool,
            "occurrences": self.occurrences,
            "time_pattern": self.time_pattern,
            "sample_requests": self.sample_requests,
            "confidence": self.confidence,
        }


class RoutineMiner:
    """Mines task history for repeated patterns and surfaces suggestions.

    This is purely observational — it never creates automations or takes actions.
    All suggestions are Prepare-tier: shown to the user for explicit approval.
    """

    def __init__(
        self,
        task_store: Any,
        min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> None:
        self.task_store = task_store
        self.min_occurrences = min_occurrences
        self.window_days = window_days

    def find_routines(self) -> list[RoutinePattern]:
        """Scan task history for repeated patterns and return suggestions.

        Returns a list of RoutinePattern objects, sorted by confidence
        (highest first). Each pattern is a potential automation suggestion
        that the user must explicitly approve.
        """
        tasks = self._get_recent_tasks()
        if len(tasks) < self.min_occurrences:
            return []

        patterns: list[RoutinePattern] = []

        # Strategy 1: Same tool used repeatedly with similar requests
        patterns.extend(self._find_tool_patterns(tasks))

        # Strategy 2: Time-based patterns (same action at similar times)
        patterns.extend(self._find_time_patterns(tasks))

        # Deduplicate and sort by confidence
        seen = set()
        unique = []
        for p in patterns:
            key = (p.tool, p.description)
            if key not in seen:
                seen.add(key)
                unique.append(p)

        unique.sort(key=lambda p: p.confidence, reverse=True)
        return unique

    def _get_recent_tasks(self) -> list[TaskRecord]:
        """Retrieve recent tasks from the store."""
        # Try to get tasks from the store's task table
        try:
            if hasattr(self.task_store, "list_tasks"):
                tasks = self.task_store.list_tasks(limit=200)
                return [
                    TaskRecord(
                        task_id=t.get("task_id", ""),
                        request=t.get("request", ""),
                        tool=t.get("tool"),
                        created_at=self._parse_dt(t.get("created_at")),
                        status=t.get("status", "completed"),
                    )
                    for t in tasks
                ]
        except Exception as exc:
            logger.warning("Failed to list tasks from store: %s", exc)

        # Fallback: try the memory store for ROUTINES category
        try:
            if hasattr(self.task_store, "list"):
                from packages.shared.schemas import MemoryCategory

                memories = self.task_store.list(category=MemoryCategory.ROUTINES)
                return [
                    TaskRecord(
                        task_id=m.memory_id,
                        request=m.content,
                        tool=None,
                        created_at=m.created_at,
                        status="completed",
                    )
                    for m in memories
                ]
        except Exception:
            pass

        return []

    def _find_tool_patterns(self, tasks: list[TaskRecord]) -> list[RoutinePattern]:
        """Find patterns where the same tool is used with similar requests."""
        # Group tasks by tool
        tool_groups: dict[str, list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            if t.tool:
                tool_groups[t.tool].append(t)

        patterns = []
        for tool, group in tool_groups.items():
            if len(group) < self.min_occurrences:
                continue

            # Cluster by request similarity
            clusters = self._cluster_by_similarity(group)
            for cluster in clusters:
                if len(cluster) < self.min_occurrences:
                    continue

                # Determine time pattern
                time_pat = self._detect_time_pattern(cluster)

                # Compute confidence based on consistency
                confidence = min(1.0, len(cluster) / 10.0)
                if time_pat:
                    confidence = min(1.0, confidence + 0.2)

                # Build description
                sample = cluster[0].request
                desc = f"you often ask to {self._simplify_request(sample)}"

                pattern = RoutinePattern(
                    pattern_id=f"tool_{tool}_{len(patterns)}",
                    description=desc,
                    tool=tool,
                    occurrences=len(cluster),
                    time_pattern=time_pat,
                    sample_requests=[t.request for t in cluster[:5]],
                    confidence=confidence,
                )
                patterns.append(pattern)

        return patterns

    def _find_time_patterns(self, tasks: list[TaskRecord]) -> list[RoutinePattern]:
        """Find patterns that occur at similar times of day/day of week."""
        if not any(t.created_at for t in tasks):
            return []

        # Group by hour of day
        hour_groups: dict[int, list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            if t.created_at:
                hour_groups[t.created_at.hour].append(t)

        patterns = []
        for hour, group in hour_groups.items():
            if len(group) < self.min_occurrences:
                continue

            # Check if there's a dominant tool in this hour
            tool_counts = Counter(t.tool for t in group if t.tool)
            if not tool_counts:
                continue

            dominant_tool, count = tool_counts.most_common(1)[0]
            if count < self.min_occurrences:
                continue

            time_label = self._hour_to_label(hour)
            desc = f"you often use {dominant_tool} during {time_label}"

            pattern = RoutinePattern(
                pattern_id=f"time_{hour}_{dominant_tool}",
                description=desc,
                tool=dominant_tool,
                occurrences=count,
                time_pattern=time_label,
                sample_requests=[t.request for t in group[:3]],
                confidence=min(1.0, count / 8.0),
            )
            patterns.append(pattern)

        return patterns

    def _cluster_by_similarity(self, tasks: list[TaskRecord]) -> list[list[TaskRecord]]:
        """Group tasks by request text similarity (simple word overlap)."""
        clusters: list[list[TaskRecord]] = []

        for task in tasks:
            words = set(self._tokenize(task.request))
            placed = False

            for cluster in clusters:
                # Compare with cluster centroid (first task's words)
                centroid_words = set(self._tokenize(cluster[0].request))
                overlap = len(words & centroid_words)
                total = len(words | centroid_words)

                if total > 0 and overlap / total > 0.4:
                    cluster.append(task)
                    placed = True
                    break

            if not placed:
                clusters.append([task])

        return clusters

    def _detect_time_pattern(self, tasks: list[TaskRecord]) -> str | None:
        """Detect if tasks follow a time pattern."""
        if not any(t.created_at for t in tasks):
            return None

        hours = [t.created_at.hour for t in tasks if t.created_at]
        if not hours:
            return None

        # Check if most tasks fall in the same 3-hour window
        hour_counts = Counter(hours)
        most_common_hour, count = hour_counts.most_common(1)[0]
        if count >= len(tasks) * 0.6:
            return self._hour_to_label(most_common_hour)

        return None

    def _hour_to_label(self, hour: int) -> str:
        """Convert an hour to a human-readable time label."""
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"

    def _simplify_request(self, request: str) -> str:
        """Simplify a request to a short description."""
        # Remove common filler words
        fillers = {"please", "can you", "could you", "would you", "i want to", "i need to", "hey vioris"}
        text = request.lower()
        for filler in fillers:
            text = text.replace(filler, "")
        text = text.strip().rstrip(".!?")
        # Truncate if too long
        if len(text) > 60:
            text = text[:57] + "..."
        return text

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization for similarity comparison."""
        return re.findall(r"\b\w+\b", text.lower())

    def _parse_dt(self, value: Any) -> datetime | None:
        """Parse a datetime from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        return None
