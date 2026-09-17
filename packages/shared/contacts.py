"""Contact Resolution — maps names/aliases to phone, email, address."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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
        if q in [a.lower() for a in self.aliases]:
            return 0.95
        if q in self.name.lower():
            return 0.8
        for alias in self.aliases:
            if q in alias.lower():
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
