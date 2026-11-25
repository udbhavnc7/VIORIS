"""Contact Resolution — maps names/aliases/relationships to phone, email, address."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Spoken relationship references are normalised before matching, so "my wife",
# "the spouse" and "wife" all resolve to the same canonical relationship.
_RELATIONSHIP_PREFIXES = ("my", "our", "the", "their", "his", "her")
# Canonical form is the first entry of each group (stable across processes).
_RELATIONSHIP_GROUPS: tuple[tuple[str, ...], ...] = (
    ("spouse", "wife", "husband", "partner"),
    ("manager", "boss", "supervisor"),
    ("parent", "mother", "father", "mom", "dad"),
    ("sibling", "brother", "sister"),
    ("child", "son", "daughter"),
)
_RELATIONSHIP_CANONICAL = {
    word: group[0]
    for group in _RELATIONSHIP_GROUPS
    for word in group
}


def normalise_relationship(value: str) -> str:
    """Strip determiners/possessives and canonicalise a relationship phrase."""
    text = re.sub(r"[^a-z\s-]", " ", value.lower().strip())
    words = text.split()
    while words and words[0] in _RELATIONSHIP_PREFIXES:
        words.pop(0)
    phrase = " ".join(words)
    return " ".join(_RELATIONSHIP_CANONICAL.get(w, w) for w in phrase.split())


def relationship_phrases(value: str) -> list[str]:
    """Surface forms of a relationship to look for in an utterance.

    Longest first, so "my wife" wins over "wife" when extracting a message body.
    """
    raw = value.lower().strip()
    if not raw:
        return []
    forms = {raw}
    canonical = normalise_relationship(raw)
    if canonical:
        forms.add(canonical)
        for word in canonical.split():
            for group in _RELATIONSHIP_GROUPS:
                if word in group:
                    forms.update(group)
    phrases = {
        form
        for phrase in forms
        for form in (phrase, *(f"{prefix} {phrase}" for prefix in _RELATIONSHIP_PREFIXES))
    }
    return sorted(phrases, key=lambda p: (-len(p), p))


@dataclass
class Contact:
    name: str
    phone: str = ""
    email: str = ""
    address: str = ""
    aliases: list[str] = field(default_factory=list)
    relationship: str = ""  # friend, family, colleague, etc.
    priority: int = 0  # higher = more likely match

    def matches(self, query: str) -> float:
        q = query.lower().strip()
        if q == self.name.lower():
            return 1.0
        aliases = [a.lower().strip() for a in self.aliases]
        if q in aliases:
            return 0.95
        # A relationship reference ("my wife", "the spouse") is specific, but
        # deliberately weaker than an explicit alias and stronger than a bare
        # substring of a name.
        if self.relationship:
            rel = normalise_relationship(self.relationship)
            rel_query = normalise_relationship(q)
            if rel and rel_query:
                if rel == rel_query:
                    return 0.9
                rel_words, query_words = set(rel.split()), set(rel_query.split())
                if rel_words & query_words and (
                    rel_words <= query_words or query_words <= rel_words
                ):
                    return 0.85
        if q in self.name.lower():
            return 0.8
        for alias in aliases:
            if q in alias:
                return 0.7
        return 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "aliases": self.aliases,
            "relationship": self.relationship,
            "priority": self.priority,
        }


class ContactBook:
    """In-memory contact store with fuzzy matching."""

    def __init__(self, contacts: Optional[list[Contact]] = None):
        self._contacts: list[Contact] = contacts or []

    def add(self, contact: Contact):
        self._contacts.append(contact)

    def remove(self, name: str) -> bool:
        for i, c in enumerate(self._contacts):
            if c.name.lower() == name.lower():
                self._contacts.pop(i)
                return True
        return False

    def find(self, query: str, threshold: float = 0.5) -> list[Contact]:
        scored = []
        for contact in self._contacts:
            score = contact.matches(query)
            if score >= threshold:
                scored.append((score, contact))
        scored.sort(key=lambda x: (-x[0], -x[1].priority))
        return [c for _, c in scored]

    def best_match(self, query: str) -> Optional[Contact]:
        results = self.find(query, threshold=0.5)
        return results[0] if results else None

    def resolve_target(self, target: str) -> dict:
        contact = self.best_match(target)
        if not contact:
            return {"resolved": False, "target": target, "error": "Contact not found"}

        channels = {}
        if contact.phone:
            channels["phone"] = contact.phone
        if contact.email:
            channels["email"] = contact.email
        if contact.address:
            channels["address"] = contact.address

        return {
            "resolved": True,
            "name": contact.name,
            "channels": channels,
            "relationship": contact.relationship,
            "primary_channel": "phone" if contact.phone else "email" if contact.email else "",
        }

    def list_all(self) -> list[Contact]:
        return list(self._contacts)

    def count(self) -> int:
        return len(self._contacts)

    def save(self, path: str):
        data = [c.to_dict() for c in self._contacts]
        Path(path).write_text(json.dumps(data, indent=2))

    def load(self, path: str):
        if not os.path.exists(path):
            return
        data = json.loads(Path(path).read_text())
        self._contacts.clear()
        for d in data:
            self._contacts.append(Contact(
                name=d["name"],
                phone=d.get("phone", ""),
                email=d.get("email", ""),
                address=d.get("address", ""),
                aliases=d.get("aliases", []),
                relationship=d.get("relationship", ""),
                priority=int(d.get("priority", 0)),
            ))


class ContactResolver:
    """Resolves targets in utterances to concrete contacts."""

    def __init__(self, book: ContactBook):
        self.book = book

    def resolve(self, target: str) -> dict:
        return self.book.resolve_target(target)

    def resolve_all(self, targets: list[str]) -> list[dict]:
        return [self.resolve(t) for t in targets]

    def suggest_contacts(self, partial: str) -> list[str]:
        results = self.book.find(partial, threshold=0.3)
        return [c.name for c in results[:5]]
