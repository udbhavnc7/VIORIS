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
