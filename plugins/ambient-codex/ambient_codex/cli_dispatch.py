"""Immutable command registry and top-level CLI parsing orchestration."""

import argparse
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    handler: str
    needs_key: bool
    pre_env: bool = False
    keyless_route: str = None


_DEFINITIONS = (
    CommandDefinition("version", "_cmd_version", False, pre_env=True),
    CommandDefinition("models", "_cmd_models_keyless", False),
    CommandDefinition("curate", "cmd_curate", False),
    CommandDefinition("setup", "cmd_setup", False),
    CommandDefinition("link", "cmd_link", False),
    CommandDefinition("uninstall", "cmd_uninstall", False),
    CommandDefinition("cache", "cmd_cache", False),
    CommandDefinition("trust-url", "_cmd_trust_url_dispatch", False),
    CommandDefinition("usage", "cmd_usage", False),
    CommandDefinition("mode", "cmd_mode", False),
    CommandDefinition("config", "cmd_config", False),
    CommandDefinition("control", "cmd_control", False),
    CommandDefinition("doctor", "cmd_doctor", False),
    CommandDefinition("use", "cmd_use", True),
    CommandDefinition("ask", "cmd_ask", True),
    CommandDefinition(
        "audit", "cmd_audit", True, keyless_route="_audit_keyless_route"),
    CommandDefinition("map", "cmd_map", True),
    CommandDefinition("code", "cmd_code", True),
    CommandDefinition("chat", "cmd_chat", True),
    CommandDefinition("build", "cmd_build", True),
    CommandDefinition("agent", "cmd_agent", True),
    CommandDefinition("codex", "_cmd_codex_keyless", False),
)

COMMAND_NAMES = tuple(definition.name for definition in _DEFINITIONS)


def make_command_registry(configurers):
    """Bind validated parser callbacks to immutable command definitions."""
    missing = tuple(name for name in COMMAND_NAMES if name not in configurers)
    if missing:
        raise ValueError("missing command configurers: " + ", ".join(missing))
    return tuple(_bind_definition(definition, configurers[definition.name])
                 for definition in _DEFINITIONS)


def _bind_definition(definition, configure):
    if not callable(configure):
        raise TypeError(f"configurer for {definition.name!r} must be callable")
    values = {
        "name": definition.name,
        "needs_key": definition.needs_key,
        "configure": configure,
        "handler": definition.handler,
    }
    if definition.pre_env:
        values = {**values, "pre_env": True}
    if definition.keyless_route:
        values = {**values, "keyless_route": definition.keyless_route}
    return MappingProxyType(values)


def build_parser(*, parser_class, description, version, commands):
    parser = parser_class(
        prog="ambient", description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", "-V", action="version",
                        version=f"ambient {version}")
    sub = parser.add_subparsers(
        dest="command", required=False, metavar="<command>",
        parser_class=parser_class)
    for spec in commands:
        spec["configure"](sub)
    return parser


def parse_args_with_stdin_dash(parser, argv=None):
    """Parse argv while accepting a lone trailing stdin sentinel."""
    args, extras = parser.parse_known_args(argv)
    if extras == ["-"]:
        prompt = getattr(args, "prompt", None)
        if isinstance(prompt, list) and "-" not in prompt:
            return argparse.Namespace(**{**vars(args), "prompt": [*prompt, "-"]})
        return args
    if extras:
        parser.error("unrecognized arguments: " + " ".join(extras))
    return args


def find_command(commands, name):
    return next((spec for spec in commands if spec["name"] == name), None)


__all__ = ("COMMAND_NAMES", "build_parser", "find_command",
           "make_command_registry", "parse_args_with_stdin_dash")
