"""Strict, credential-free tool schemas for the Ambient MCP server."""

from typing import Any, Dict, List, Optional

MAX_PROMPT_CHARS = 60_000
MAX_SYSTEM_CHARS = 10_000
MAX_PATHS = 25

def empty_schema() -> Dict[str, Any]:
    return tool_schema({})


def tool_schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "ambient_status",
        "description": "Show local Ambient configuration, key state, model defaults, and session mode.",
        "inputSchema": empty_schema(),
    },
    {
        "name": "ambient_control",
        "description": "Return the Codex-native Ambient control snapshot as JSON.",
        "inputSchema": tool_schema({
            "all_models": {
                "type": "boolean",
                "description": "Include models hidden by local curation.",
            },
            "offline": {
                "type": "boolean",
                "description": "Skip catalog fetch and show only local state.",
            },
        }),
    },
    {
        "name": "ambient_set_mode",
        "description": "Set this Codex session to Normal Codex (off), Delegate (on), or Ambient session (takeover).",
        "inputSchema": tool_schema({
            "state": {"type": "string", "enum": ["off", "on", "takeover"]},
        }, required=["state"]),
    },
    {
        "name": "ambient_set_model",
        "description": "Set the default Ambient model for chat, code, or both lanes.",
        "inputSchema": tool_schema({
            "model": {"type": "string", "maxLength": 256},
            "lane": {"type": "string", "enum": ["both", "chat", "code"]},
        }, required=["model", "lane"]),
    },
    {
        "name": "ambient_pick_model",
        "description": (
            "Let the user pick the Ambient model from a native Codex picker listing "
            "models serving right now. For the normal chat menu, show a browse-all "
            "option that uses `ambient_models` with `all=true` for on-demand models. "
            "The picker asks exactly one "
            "question. `lane` is NOT elicited: omit it to apply the pick to both the "
            "chat and code lanes, or pass chat/code when the user already said which. "
            "Falls back to a numbered menu on clients without a picker."
        ),
        "inputSchema": tool_schema({
            "lane": {"type": "string", "enum": ["both", "chat", "code"]},
        }),
    },
    {
        "name": "ambient_pick_mode",
        "description": (
            "Let the user pick Normal Codex, Delegate, or Ambient session from a native "
            "Codex picker. Use when the user wants to change how much work routes to "
            "Ambient without naming a mode. The choice lasts only for this session. "
            "Falls back to a numbered menu on clients without a picker."
        ),
        "inputSchema": tool_schema({}),
    },
    {
        "name": "ambient_set_config",
        "description": "Set or unset a whitelisted Ambient config knob.",
        "inputSchema": tool_schema({
            "name": {
                "type": "string",
                "enum": [
                    "streaming",
                    "fallback",
                    "savings",
                ],
            },
            "value": {"type": "string", "maxLength": 128},
            "unset": {"type": "boolean"},
        }, required=["name"]),
    },
    {
        "name": "ambient_key",
        "description": "Show key status, print terminal-only setup/rotation instructions, or remove the stored key. Never accepts key material.",
        "inputSchema": tool_schema({
            "action": {"type": "string", "enum": ["status", "setup", "rotate", "remove"]},
        }, required=["action"]),
    },
    {
        "name": "ambient_models",
        "description": "List Ambient models. Defaults to serving/catalog JSON.",
        "inputSchema": tool_schema({
            "all": {"type": "boolean", "description": "Include the full catalog."},
            "json": {"type": "boolean", "description": "Return CLI JSON output."},
        }),
    },
    {
        "name": "ambient_doctor",
        "description": "Run Ambient diagnostics for key, funds, model, network, and service issues.",
        "inputSchema": empty_schema(),
    },
    {
        "name": "ambient_usage",
        "description": "Show local Ambient usage and relative savings estimates.",
        "inputSchema": tool_schema({
            "days": {"type": "integer", "minimum": 1, "maximum": 3650},
            "json": {"type": "boolean"},
        }),
    },
    {
        "name": "ambient_self_test",
        "description": "Run a local no-network plugin/MCP startup self-test.",
        "inputSchema": empty_schema(),
    },
    {
        "name": "ambient_ask",
        "description": "Run a short one-shot Ambient ask through the CLI.",
        "inputSchema": tool_schema({
            "prompt": {"type": "string", "maxLength": MAX_PROMPT_CHARS},
            "system": {"type": "string", "maxLength": MAX_SYSTEM_CHARS},
            "model": {"type": "string", "maxLength": 256},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
            "json": {"type": "boolean"},
        }, required=["prompt"]),
    },
    {
        "name": "ambient_audit_small",
        "description": "Audit a small bounded path list, staged diff, or diff ref. Use shell CLI for repo audits.",
        "inputSchema": tool_schema({
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 1000},
                "maxItems": MAX_PATHS,
            },
            "staged": {"type": "boolean"},
            "diff": {"type": "string", "maxLength": 200},
            "focus": {"type": "string", "maxLength": 300},
            "cwd": {"type": "string", "maxLength": 4096},
            "json": {"type": "boolean"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
        }),
    },
]


__all__ = ("MAX_PATHS", "MAX_PROMPT_CHARS", "MAX_SYSTEM_CHARS", "TOOLS",
           "empty_schema", "tool_schema")
