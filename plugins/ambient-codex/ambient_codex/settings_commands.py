"""Injected, keyless mode and user-settings command orchestration."""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ModeDependencies:
    save_config: object
    read_config: object
    resolve_key: object
    resolve_model: object
    launcher_name: str


@dataclass(frozen=True)
class ConfigDependencies:
    settings: Sequence[Mapping]
    save_config: object
    read_config: object
    resolve_key: object
    resolve_model: object
    usage_error: object
    launcher_name: str
    secret_patterns: Sequence[object]
    environ: Mapping[str, str]


@dataclass(frozen=True)
class ControlDependencies:
    version: str
    key_console_url: str
    launcher_name: str
    mode_options: Sequence[object]
    workflows: Sequence[object]
    chat_actions: Sequence[object]
    actions: Sequence[str]
    setting_names: Sequence[str]
    settings: Sequence[Mapping]
    environ: Mapping[str, str]
    read_config: object
    resolve_key: object
    resolve_model: object
    resolve_api_url: object
    safe_catalog: object
    dedupe_catalog: object
    as_bool: object
    curation: object
    is_hidden: object
    config_curation_summary: object
    config_redact: object
    usage_error: object
    command_setup: object
    command_use: object
    command_config: object
    command_mode: object
    command_doctor: object
    command_usage: object


def normalize_bool(raw):
    value = str(raw).strip().lower()
    if value in ("on", "true", "1", "yes"):
        return "on"
    if value in ("off", "false", "0", "no"):
        return "off"
    raise ValueError("expected on or off")


def normalize_price(raw, parse_reference_price):
    value = str(raw).strip()
    if parse_reference_price(value) is None:
        raise ValueError("expected an in/out pair like 3/15, or one number")
    return value


def format_number(value):
    return str(int(value)) if float(value).is_integer() else str(value)


def run_mode(args, deps):
    if args.state in ("on", "off", "takeover"):
        deps.save_config({"AMBIENT_DELEGATE": args.state})
        _print_mode_change(args.state)
        return
    conf = deps.read_config()
    key, backend = deps.resolve_key(conf)
    namespace = argparse.Namespace(model=None)
    print(f"delegate={(conf.get('AMBIENT_DELEGATE') or 'off').lower()}")
    print(f"chat_model={deps.resolve_model(namespace, conf, 'chat')}")
    print(f"code_model={deps.resolve_model(namespace, conf, 'code')}")
    key_state = (f"configured ({backend})" if key else
                 f"MISSING (run: {deps.launcher_name} setup)")
    print(f"key={key_state}")


def _print_mode_change(state):
    if state != "takeover":
        print(f"Ambient delegate mode: {state.upper()}")
        return
    print("Ambient Takeover: ON — Codex will route substantive work (chat, "
          "questions, building, reviews) through Ambient for the heavy "
          "model work. Codex still coordinates safety, review, and final "
          "integration.\nTurn it off any time:  ambient-codex control mode off   "
          "(or ask Codex to use Ambient off)")


def config_names(settings):
    return ", ".join(setting["name"] for setting in settings)


def redact_unknown(text, secret_patterns):
    clean = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,30}", text)
    if clean and not any(pattern.search(text) for pattern in secret_patterns):
        return text
    return "<unrecognized>"


def curation_summary(conf, environ):
    keys = ("AMBIENT_MODELS_ALLOW", "AMBIENT_MODELS_HIDE",
            "AMBIENT_MODELS_SHOW", "AMBIENT_MODEL_NOTES")
    return ("customized" if any(environ.get(key) or conf.get(key) for key in keys)
            else "default (all shown)")


def env_shadow_note(env, environ):
    if environ.get(env) is not None:
        print(f"note: {env} is set in your environment — it overrides the config "
              "file, so this change takes effect only after you unset that env var.",
              file=sys.stderr)


def print_config_status(deps):
    conf = deps.read_config()
    key, backend = deps.resolve_key(conf)
    namespace = argparse.Namespace(model=None)
    key_state = (f"configured ({backend})" if key else
                 f"MISSING — run: {deps.launcher_name} setup")
    owned = _owned_status_rows(conf, namespace, key_state, deps)
    rows = _setting_status_rows(conf, deps)
    all_rows = [*owned, *rows]
    label_width = max(len(row[0]) for row in all_rows) + 2
    value_width = max(len(str(row[1])) for row in all_rows) + 2
    print("Ambient settings\n")
    _emit_rows(owned, label_width, value_width)
    print()
    _emit_rows(rows, label_width, value_width)
    print("\n  reset any knob to its default:  ambient-codex config unset <name>")


def _owned_status_rows(conf, namespace, key_state, deps):
    launcher = deps.launcher_name
    return [
        ("API key", key_state, f"{launcher} setup --force / --remove"),
        ("Model · chat", deps.resolve_model(namespace, conf, "chat"),
         f"{launcher} use"),
        ("Model · code", deps.resolve_model(namespace, conf, "code"),
         f"{launcher} use --code"),
        ("Delegate mode", (conf.get("AMBIENT_DELEGATE") or "off").lower(),
         f"{launcher} mode on|takeover|off"),
        ("Curation", curation_summary(conf, deps.environ), f"{launcher} curate"),
    ]


def _setting_status_rows(conf, deps):
    rows = []
    for setting in deps.settings:
        value = setting["current"](conf)
        if deps.environ.get(setting["env"]) is not None:
            value += "  (env override)"
        rows = [*rows, (
            setting["name"], value,
            f"ambient-codex config set {setting['name']} {setting['how']}")]
    return rows


def _emit_rows(rows, label_width, value_width):
    for label, value, how in rows:
        print(f"  {label:<{label_width}}{str(value):<{value_width}}{how}")


def run_config(args, deps):
    verb = getattr(args, "verb", None) or "status"
    if verb == "status":
        print_config_status(deps)
        return
    setting = _resolve_setting(args, verb, deps)
    value = getattr(args, "value", None)
    if verb == "unset":
        _unset_setting(setting, value, deps)
        return
    _set_setting(setting, value, deps)


def _resolve_setting(args, verb, deps):
    name = getattr(args, "name", None)
    if not name:
        deps.usage_error(
            f"config {verb} needs a setting name (one of: "
            f"{config_names(deps.settings)})")
    bare = name.split("=", 1)[0].strip()
    if bare.lower().replace("_", "-") in (
            "key", "api-key", "apikey", "ambient-api-key"):
        deps.usage_error(
            "the API key isn't a config value — rotate it with "
            f"`{deps.launcher_name} setup --force`, remove it with "
            f"`{deps.launcher_name} setup --remove` (hidden input, in your OWN "
            "terminal). It never passes through config.")
    by_name = {setting["name"]: setting for setting in deps.settings}
    setting = by_name.get(bare.lower())
    if setting is None:
        safe = redact_unknown(bare, deps.secret_patterns)
        deps.usage_error(
            f"unknown setting '{safe}'. Valid: {config_names(deps.settings)}. "
            "(Model → ambient-codex use · delegate mode → ambient-codex mode · "
            f"curation → {deps.launcher_name} curate · endpoint → "
            f"{deps.launcher_name} trust-url · key → {deps.launcher_name} setup)")
    return setting


def _unset_setting(setting, value, deps):
    if value is not None:
        deps.usage_error(f"config unset {setting['name']} takes no value")
    deps.save_config({setting["env"]: None})
    print(f"{setting['name']} → back to default ({setting['default']})")
    env_shadow_note(setting["env"], deps.environ)


def _set_setting(setting, value, deps):
    if value is None:
        deps.usage_error(
            f"config set {setting['name']} needs a value ({setting['how']})")
    try:
        normalized = setting["validate"](value)
    except ValueError as error:
        deps.usage_error(f"invalid value for {setting['name']}: {error}")
    deps.save_config({setting["env"]: normalized})
    print(f"{setting['name']} = {normalized}")
    env_shadow_note(setting["env"], deps.environ)


def control_catalog(conf, deps, include_all=False, offline=False):
    if offline:
        return []
    api_key, _backend = deps.resolve_key(conf)
    try:
        api_url = deps.resolve_api_url(conf)
    except SystemExit:
        return []
    models = [model for model in deps.dedupe_catalog(
        deps.safe_catalog(api_url, api_key or "none"))
        if isinstance(model, dict) and model.get("id")]
    models = sorted(
        models, key=lambda model: (
            not deps.as_bool(model.get("is_ready")), model.get("id") or ""))
    if include_all:
        return models
    allow, hide, show, _notes = deps.curation(conf)
    visible = [model for model in models
               if not deps.is_hidden(model["id"], allow, hide, show)]
    return visible or models


def control_settings(conf, deps):
    selected = [setting for setting in deps.settings
                if setting["name"] in deps.setting_names]
    return [{
        "name": setting["name"],
        "value": setting["current"](conf),
        "default": setting["default"],
        "env": setting["env"],
        "env_override": deps.environ.get(setting["env"]) is not None,
        "syntax": setting["how"],
    } for setting in selected]


def control_setting_names(setting_names):
    return ", ".join(setting_names)


def control_model_item(model, chat_default, code_default, hidden_ids, notes,
                       as_bool):
    model_id = model["id"]
    return {
        "id": model_id,
        "name": model.get("name"),
        "ready": as_bool(model.get("is_ready")),
        "context_length": model.get("context_length"),
        "max_output_length": model.get("max_output_length"),
        "features": model.get("supported_features") or [],
        "is_chat_default": model_id == chat_default,
        "is_code_default": model_id == code_default,
        "hidden": model_id in hidden_ids,
        "note": notes.get(model_id) or None,
    }


def control_snapshot(deps, include_all=False, offline=False):
    conf = deps.read_config()
    api_key, backend = deps.resolve_key(conf)
    namespace = argparse.Namespace(model=None)
    chat_default = deps.resolve_model(namespace, conf, "chat")
    code_default = deps.resolve_model(namespace, conf, "code")
    mode = (conf.get("AMBIENT_DELEGATE") or "off").lower()
    models = control_catalog(conf, deps, include_all, offline)
    allow, hide, show, notes = deps.curation(conf)
    hidden_ids = frozenset(
        model["id"] for model in models
        if deps.is_hidden(model["id"], allow, hide, show))
    items = [control_model_item(
        model, chat_default, code_default, hidden_ids, notes, deps.as_bool)
        for model in models]
    serving = [model for model in items if model["ready"]]
    return _control_snapshot_payload(
        deps, api_key, backend, mode, chat_default, code_default,
        allow, hide, show, items, serving, conf)


def _control_snapshot_payload(deps, api_key, backend, mode, chat_default,
                              code_default, allow, hide, show, items, serving,
                              conf):
    return {
        "schema_version": 1, "surface": "codex-native",
        "version": deps.version,
        "key": {"configured": bool(api_key),
                "backend": backend if api_key else None},
        "mode": mode,
        "mode_options": [
            {"state": state, "label": label, "description": description,
             "current": state == mode}
            for state, label, description in deps.mode_options],
        "defaults": {"chat": chat_default, "code": code_default},
        "settings": control_settings(conf, deps),
        "curation": {"summary": deps.config_curation_summary(conf),
                     "allow": allow, "hide": hide, "show": show},
        "models": {"available": bool(items), "serving_count": len(serving),
                   "total_count": len(items), "items": items},
        "chat_actions": [
            {"phrase": phrase, "description": description}
            for phrase, description in deps.chat_actions],
        "workflows": [
            {"phrase": phrase, "description": description}
            for phrase, description in deps.workflows],
        "actions": list(deps.actions),
    }


def print_control_status(payload, deps):
    key = payload["key"]
    key_state = (f"configured ({key['backend']})" if key["configured"] else
                 f"MISSING — get a key at {deps.key_console_url}, then run: "
                 f"{deps.launcher_name} setup")
    print("Ambient Codex Control")
    print("Native surface: Codex plugin bundle")
    print(f"API key: {key_state}")
    print(f"Mode: {payload['mode']}")
    print(f"Chat model: {payload['defaults']['chat']}")
    print(f"Code model: {payload['defaults']['code']}")
    _print_control_modes_and_settings(payload)
    _print_control_models(payload["models"])
    _print_control_actions(payload)


def _print_control_modes_and_settings(payload):
    print("\nModes:")
    for option in payload["mode_options"]:
        suffix = " (current)" if option["current"] else ""
        print(f"  {option['state']:<8} {option['label']:<9} "
              f"{option['description']}{suffix}")
    print("\nSettings:")
    for setting in payload["settings"]:
        suffix = " (env override)" if setting["env_override"] else ""
        print(f"  {setting['name']:<16} {setting['value']}{suffix}")
    print(f"\nCuration: {payload['curation']['summary']}")


def _print_control_models(models):
    if not models["available"]:
        print("\nServing models right now: unavailable in this status view")
        return
    print(f"\nServing models right now: {models['serving_count']} of "
          f"{models['total_count']} shown")
    for model in models["items"][:8]:
        state = "serving" if model["ready"] else "on demand"
        marks = [name for name, active in (
            ("chat", model["is_chat_default"]),
            ("code", model["is_code_default"])) if active]
        label = f" ({', '.join(marks)})" if marks else ""
        print(f"  {model['id']} - {state}{label}")
    if models["total_count"] > 8:
        print(f"  ... {models['total_count'] - 8} more; run: ambient-codex models")


def _print_control_actions(payload):
    print("\nWorkflows:")
    for workflow in payload["workflows"]:
        print(f"  {workflow['phrase']:<24} {workflow['description']}")
    print("\nIn Codex chat, say:")
    workflow_phrases = frozenset(
        workflow["phrase"] for workflow in payload["workflows"])
    actions = tuple(action for action in payload["chat_actions"]
                    if action["phrase"] not in workflow_phrases)
    width = max(24, *(len(action["phrase"]) + 2 for action in actions))
    for action in actions:
        print(f"  {action['phrase']:<{width}}{action['description']}")
    print("  (workflow commands are listed above)")
    print("\nActions:")
    for action in payload["actions"]:
        print(f"  {action}")


def print_control_key_status(deps):
    api_key, backend = deps.resolve_key(deps.read_config())
    if api_key:
        print(f"API key: configured ({backend})")
        print(f"Rotate: {deps.launcher_name} setup --force")
        print(f"Remove: {deps.launcher_name} setup --remove")
        return
    print("API key: MISSING")
    print(f"  1. Get a key at {deps.key_console_url}")
    print(f"  2. Run in your terminal:  {deps.launcher_name} setup")


def control_setup_instruction(action, deps):
    command = (f"{deps.launcher_name} setup --force" if action == "rotate"
               else f"{deps.launcher_name} setup --remove"
               if action == "remove" else f"{deps.launcher_name} setup")
    verb = {"rotate": "rotate your key", "remove": "remove your key"}.get(
        action, "add your key")
    print(f"To {verb}, run this in your own terminal:")
    print(f"  {command}")
    if action not in ("rotate", "remove"):
        print(f"Get a key first at: {deps.key_console_url}")
    print("The key is entered privately (hidden input, verified locally) — it never "
          "enters chat, MCP arguments, process argv, or logs.")


def control_key(args, deps):
    action = getattr(args, "key_action", None) or "status"
    if action == "status":
        print_control_key_status(deps)
    elif action == "remove":
        deps.command_setup(argparse.Namespace(
            key_stdin=False, force=False, file=False, remove=True))
    elif not sys.stdin.isatty():
        control_setup_instruction(action, deps)
    else:
        deps.command_setup(argparse.Namespace(
            key_stdin=False, force=action == "rotate",
            file=getattr(args, "file", False), remove=False))


def control_model(args, deps):
    conf = deps.read_config()
    api_key, _backend = deps.resolve_key(conf)
    deps.command_use(argparse.Namespace(
        model_id=getattr(args, "model_id", None),
        chat=getattr(args, "chat", False),
        code=getattr(args, "code", False),
        all=getattr(args, "all", False), yes=True,
    ), api_key or "none", deps.resolve_api_url(conf), conf)


def control_setting(args, deps):
    name = (getattr(args, "name", None) or "").strip().lower()
    if name not in deps.setting_names:
        deps.usage_error(
            f"unknown control setting '{deps.config_redact(name)}'. Valid: "
            f"{control_setting_names(deps.setting_names)}. Advanced settings "
            f"live under `{deps.launcher_name} config`.")
    value = getattr(args, "value", None)
    if getattr(args, "unset", False):
        deps.command_config(argparse.Namespace(
            verb="unset", name=name, value=None))
    elif value is None:
        deps.usage_error("control setting needs a value, or pass --unset")
    else:
        deps.command_config(argparse.Namespace(
            verb="set", name=name, value=value))


def control_menu(args, deps):
    if not sys.stdin.isatty():
        payload = control_snapshot(
            deps, getattr(args, "all_models", False),
            getattr(args, "offline", False))
        print_control_status(payload, deps)
        return
    while True:
        _print_control_menu()
        choice = input("Select: ").strip()
        if choice in ("0", "q", "quit", "exit"):
            return
        _handle_control_menu_choice(choice, deps)


def _print_control_menu():
    print("\nAmbient Codex Control")
    for line in ("1. Status", "2. Mode", "3. Chat model", "4. Code model",
                 "5. API key", "6. Setting", "7. Doctor", "8. Usage",
                 "0. Exit"):
        print(f"  {line}")


def _handle_control_menu_choice(choice, deps):
    handlers = {
        "1": lambda: print_control_status(control_snapshot(deps), deps),
        "2": lambda: _menu_mode(deps),
        "3": lambda: control_model(argparse.Namespace(
            model_id=None, chat=True, code=False, all=False), deps),
        "4": lambda: control_model(argparse.Namespace(
            model_id=None, chat=False, code=True, all=False), deps),
        "5": lambda: _menu_key(deps),
        "6": lambda: _menu_setting(deps),
        "7": lambda: deps.command_doctor(argparse.Namespace()),
        "8": lambda: deps.command_usage(argparse.Namespace(days=30, json=False)),
    }
    handler = handlers.get(choice)
    if handler:
        handler()
    else:
        print("unknown choice")


def _menu_mode(deps):
    state = input("Mode (off/on/takeover): ").strip().lower()
    if state in ("off", "on", "takeover"):
        deps.command_mode(argparse.Namespace(state=state))
    else:
        print("cancelled - expected off, on, or takeover")


def _menu_key(deps):
    action = input("Key action (status/setup/rotate/remove): ").strip().lower()
    if action in ("status", "setup", "add", "rotate", "remove"):
        control_key(argparse.Namespace(key_action=action, file=False), deps)
    else:
        print("cancelled - expected status, setup, rotate, or remove")


def _menu_setting(deps):
    print("Settings: " + control_setting_names(deps.setting_names))
    name = input("Name: ").strip()
    value = input("Value (blank to unset): ").strip()
    control_setting(argparse.Namespace(
        name=name, value=value or None, unset=not bool(value)), deps)


def run_control(args, deps):
    action = getattr(args, "control_action", None) or "status"
    if action == "status":
        payload = control_snapshot(
            deps, getattr(args, "all_models", False),
            getattr(args, "offline", False))
        print(json.dumps(payload, indent=2)) if getattr(args, "json", False) \
            else print_control_status(payload, deps)
        return
    handlers = {
        "menu": lambda: control_menu(args, deps),
        "mode": lambda: deps.command_mode(argparse.Namespace(
            state=getattr(args, "state", None))),
        "model": lambda: control_model(args, deps),
        "key": lambda: control_key(args, deps),
        "setting": lambda: control_setting(args, deps),
        "doctor": lambda: deps.command_doctor(argparse.Namespace()),
        "usage": lambda: deps.command_usage(argparse.Namespace(
            days=getattr(args, "days", 30),
            json=getattr(args, "json", False))),
    }
    handler = handlers.get(action)
    if handler is None:
        deps.usage_error(f"unknown control action: {action}")
    handler()


__all__ = ("ConfigDependencies", "ControlDependencies", "ModeDependencies",
           "config_names", "control_catalog", "control_model_item",
           "control_setting_names", "control_settings", "control_snapshot",
           "curation_summary", "env_shadow_note", "format_number",
           "normalize_bool", "normalize_price", "print_config_status",
           "print_control_status", "redact_unknown", "run_config",
           "run_control", "run_mode")
