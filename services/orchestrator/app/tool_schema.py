"""
Fixed tool schema for the planner (Phase 2, Prompt 2.2).

The planner exposes ONLY the statically registered tools to the LLM, shaped as
JSON function schemas. The registry (packages/shared/permission_engine) is the
single source of truth for what can even be proposed; the LLM can pick from it,
argue arguments for it, and order the steps — it cannot invent tools and it
cannot set risk levels.

Parameter schemas are kept explicit here (and stay in sync with the registry by
name). Unknown tools never reach this table.
"""

from __future__ import annotations

from packages.shared.permission_engine import PermissionEngine

# Which arguments each Phase 2-capable tool accepts, in JSON-schema form.
# Everything is phase-1 local tools (no account access). Arg schemas are loose by
# design so a weak local LLM can still fill them.
TOOL_PARAMETERS: dict[str, dict] = {
    "system.get_time": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "system.open_app": {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "name of the desktop application, e.g. 'vs code', 'chrome'",
            }
        },
        "required": ["app"],
        "additionalProperties": False,
    },
    "system.set_reminder": {
        "type": "object",
        "properties": {
            "reminder": {"type": "string", "description": "what to be reminded of"},
            "at": {"type": "string", "description": "when, e.g. '6pm' or 'monday 9am'"},
        },
        "required": ["reminder", "at"],
        "additionalProperties": False,
    },
    "system.stop": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "gmail.read_unread": {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "look back window in hours (default 24)",
            }
        },
        "additionalProperties": False,
    },
    "gmail.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "whatsapp.read_digest": {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "look back window in hours (default 24)",
            }
        },
        "additionalProperties": False,
    },
    "whatsapp.send_message": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "recipient name or phone number as the user said it",
            },
            "recipient_identity": {
                "type": "string",
                "description": "resolved canonical contact identity shown on the diff card",
            },
            "content": {
                "type": "string",
                "description": "exact message text to send",
            },
            "channel": {
                "type": "string",
                "description": "delivery channel (default whatsapp)",
            },
        },
        "required": ["recipient", "content"],
        "additionalProperties": False,
    },
    "whatsapp.session_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "calendar.read_upcoming": {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "look ahead window in hours (default 72)",
            }
        },
        "additionalProperties": False,
    },
    "calendar.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "cloud_files.list_recent": {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "look back window in hours (default 72)",
            }
        },
        "additionalProperties": False,
    },
    "cloud_files.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "notes.list_recent": {
        "type": "object",
        "properties": {
            "hours": {
                "type": "integer",
                "description": "look back window in hours (default 168)",
            }
        },
        "additionalProperties": False,
    },
    "notes.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "contacts.search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "name / email / phone fragment to match"},
        },
        "additionalProperties": False,
    },
    "contacts.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "bookmarks.search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "title / url / folder fragment to match"},
        },
        "additionalProperties": False,
    },
    "bookmarks.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "history.recent": {
        "type": "object",
        "properties": {
            "hours": {"type": "integer", "description": "look-back window for recent visits"},
        },
        "additionalProperties": False,
    },
    "history.digest_status": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "telephony.prepare_call": {
        "type": "object",
        "properties": {
            "recipient": {"type": "string", "description": "contact name or number to call"},
            "purpose": {"type": "string", "description": "why you're calling (feeds the brief)"},
        },
        "required": ["recipient"],
        "additionalProperties": False,
    },
    "telephony.start_call": {
        "type": "object",
        "properties": {
            "recipient": {"type": "string", "description": "contact name or number as the user said it"},
            "recipient_identity": {"type": "string", "description": "resolved canonical contact identity shown on the diff card"},
            "phone": {"type": "string", "description": "resolved phone number shown on the diff card"},
            "script": {"type": "string", "description": "draft script to speak (handed to the dialer with the number)"},
        },
        "required": ["recipient"],
        "additionalProperties": False,
    },
    "reservations.search_slots": {
        "type": "object",
        "properties": {
            "venue": {"type": "string", "description": "restaurant or appointment name"},
            "date": {"type": "string", "description": "target date"},
            "party_size": {"type": "integer", "description": "number of guests"},
        },
        "required": ["venue"],
        "additionalProperties": False,
    },
    "reservations.create": {
        "type": "object",
        "properties": {
            "slot_id": {"type": "string", "description": "chosen slot id from search_slots"},
            "venue": {"type": "string", "description": "restaurant or appointment name (shown on diff card)"},
            "at": {"type": "string", "description": "exact date/time (shown on diff card)"},
            "party_size": {"type": "integer", "description": "number of guests (shown on diff card)"},
            "guest_name": {"type": "string", "description": "name to book under (shown on diff card)"},
        },
        "required": ["slot_id", "guest_name"],
        "additionalProperties": False,
    },
    "smart_home.device_status": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "optional filter: light, climate, camera, energy, ..."},
        },
        "additionalProperties": False,
    },
    "smart_home.control": {
        "type": "object",
        "properties": {
            "device_id": {"type": "string", "description": "device to control (shown on diff card)"},
            "device_name": {"type": "string", "description": "human-readable device name (shown on diff card)"},
            "category": {"type": "string", "description": "device category (safety-critical ones are refused)"},
            "action": {"type": "string", "description": "exact action, e.g. on/off/brighter (shown on diff card)"},
            "value": {"type": "string", "description": "target value, e.g. on/off/50 (shown on diff card)"},
        },
        "required": ["device_id", "action", "value"],
        "additionalProperties": False,
    },
}


def list_registered_tools() -> list[str]:
    """Registered tool names only, from the frozen registry."""
    return sorted(reg.tool_name for reg in PermissionEngine.list_tools())


def build_tool_schema() -> list[dict]:
    """OpenAI-style function list derived from the frozen registry.

    Any registered tool that has no parameter schema defined is still exposed
    with an empty object schema, so the registry stays the source of truth.
    """
    schema: list[dict] = []
    for name in list_registered_tools():
        params = TOOL_PARAMETERS.get(
            name, {"type": "object", "properties": {}, "additionalProperties": False}
        )
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _description(name),
                    "parameters": params,
                },
            }
        )
    return schema


def _description(tool_name: str) -> str:
    for reg in PermissionEngine.list_tools():
        if reg.tool_name == tool_name:
            return reg.description
    return tool_name


def assert_known_tool(tool_name: str) -> None:
    """Raise if the LLM proposed a tool that is not in the frozen registry."""
    if not PermissionEngine.is_registered(tool_name):
        raise KeyError(f"'{tool_name}' is not a registered tool")
