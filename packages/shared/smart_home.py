"""Smart Home Connector — Home Assistant API wrapper."""

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional


class EntityState(Enum):
    ON = "on"
    OFF = "off"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str = ""
    last_updated: str = ""

    def is_on(self) -> bool:
        return self.state.lower() in ("on", "open", "playing", "home", "locked")

    def is_off(self) -> bool:
        return self.state.lower() in ("off", "closed", "stopped", "away", "unlocked")

    def get_attribute(self, key: str, default: Any = None) -> Any:
        return self.attributes.get(key, default)

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_changed": self.last_changed,
            "last_updated": self.last_updated,
        }


@dataclass
class ServiceCall:
    domain: str
    service: str
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"entity_id": self.entity_id, **self.data}
        return d


class HomeAssistantConnector:
    """Home Assistant REST API wrapper."""

    def __init__(self, base_url: str = "", token: str = ""):
        self.base_url = base_url or os.getenv("HA_URL", "http://localhost:8123")
        self.token = token or os.getenv("HA_TOKEN", "")
        self._connected = False
        self._states: dict[str, EntityState] = {}

    def connect(self) -> tuple[bool, str]:
        if not self.token:
            return False, "No token configured"
        self._connected = True
        return True, "Connected"

    def is_connected(self) -> bool:
        return self._connected

    def get_state(self, entity_id: str) -> Optional[EntityState]:
        return self._states.get(entity_id)

    def get_states(self, domain: Optional[str] = None) -> list[EntityState]:
        states = list(self._states.values())
        if domain:
            states = [s for s in states if s.entity_id.startswith(f"{domain}.")]
        return states

    def set_state(self, entity_id: str, state: str, attributes: Optional[dict] = None):
        existing = self._states.get(entity_id)
        now = datetime.now(UTC).isoformat()
        if existing:
            existing.state = state
            existing.last_changed = now
            existing.last_updated = now
            if attributes:
                existing.attributes.update(attributes)
        else:
            self._states[entity_id] = EntityState(
                entity_id=entity_id,
                state=state,
                attributes=attributes or {},
                last_changed=now,
                last_updated=now,
            )

    def call_service(self, call: ServiceCall) -> tuple[bool, str]:
        if not self._connected:
            return False, "Not connected"

        entity_id = call.entity_id
        if call.domain == "light":
            if call.service == "turn_on":
                self.set_state(entity_id, "on", call.data)
            elif call.service == "turn_off":
                self.set_state(entity_id, "off")
            elif call.service == "toggle":
                current = self.get_state(entity_id)
                if current and current.is_on():
                    self.set_state(entity_id, "off")
                else:
                    self.set_state(entity_id, "on", call.data)

        elif call.domain == "switch":
            if call.service == "turn_on":
                self.set_state(entity_id, "on")
            elif call.service == "turn_off":
                self.set_state(entity_id, "off")

        elif call.domain == "climate":
            if call.service == "set_temperature":
                self.set_state(entity_id, "heat", {"temperature": call.data.get("temperature", 22)})
            elif call.service == "set_hvac_mode":
                self.set_state(entity_id, call.data.get("hvac_mode", "off"))

        elif call.domain == "media_player":
            if call.service == "play":
                self.set_state(entity_id, "playing", call.data)
            elif call.service == "pause":
                self.set_state(entity_id, "paused")
            elif call.service == "stop":
                self.set_state(entity_id, "stopped")

        elif call.domain == "lock":
            if call.service == "lock":
                self.set_state(entity_id, "locked")
            elif call.service == "unlock":
                self.set_state(entity_id, "unlocked")

        elif call.domain == "cover":
            if call.service == "open_cover":
                self.set_state(entity_id, "open")
            elif call.service == "close_cover":
                self.set_state(entity_id, "closed")

        return True, f"{call.domain}.{call.service} executed"

    def get_light_state(self, entity_id: str) -> dict:
        state = self.get_state(entity_id)
        if not state:
            return {"on": False}
        return {
            "on": state.is_on(),
            "brightness": state.get_attribute("brightness", 255),
            "color_temp": state.get_attribute("color_temp"),
            "rgb_color": state.get_attribute("rgb_color"),
        }

    def get_climate_state(self, entity_id: str) -> dict:
        state = self.get_state(entity_id)
        if not state:
            return {"mode": "off", "temperature": 22}
        return {
            "mode": state.state,
            "temperature": state.get_attribute("temperature", 22),
            "humidity": state.get_attribute("humidity"),
            "hvac_action": state.get_attribute("hvac_action"),
        }

    def get_lock_state(self, entity_id: str) -> dict:
        state = self.get_state(entity_id)
        if not state:
            return {"locked": False}
        return {"locked": state.state == "locked"}

    def list_devices(self, domain: Optional[str] = None) -> list[dict]:
        states = self.get_states(domain)
        return [s.to_dict() for s in states]
