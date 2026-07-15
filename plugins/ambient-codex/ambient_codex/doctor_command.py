"""Injected local, network, model, and integration health diagnostics."""

import argparse
import json
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class DoctorDependencies:
    version: str
    config_path: str
    launcher_name: str
    shared_api_key_env: str
    api_key_env: str
    opencode_config_path: str
    opencode_provider: str
    cache_dir: str
    read_config: object
    resolve_key: object
    resolve_api_url: object
    sanitize: object
    paint: object
    which: object
    keychain_available: object
    shared_key_env_is_set: object
    api_request: object
    network_error: object
    classify_error: object
    catalog_data: object
    dedupe_catalog: object
    ready_model_ids: object
    auth_probe: object
    curation: object
    resolve_model: object
    is_auto_model: object
    is_hidden: object
    bundled_cli_path: object
    stable_launcher_is_ours: object


def format_check_line(check, ok, detail, paint):
    tag = paint("PASS", "32") if ok else paint("FAIL", "31")
    return f"{tag:4}  {check:14} {detail}"


def auth_status_detail(ok, category):
    """Map authentication state to fixed text that cannot reflect a response."""
    if ok:
        return "authentication verified"
    return {
        "funds": "account is out of funds or over quota",
        "key": "API key rejected",
        "rate": "authentication check rate-limited",
        "service": "Ambient service unavailable during authentication check",
    }.get(category, "authentication check failed")


def key_backend_label(backend):
    """Return only recognized local backend labels."""
    return backend if backend in {"env", "file", "keychain", "secret-tool"} \
        else "configured"


def _reporter(deps):
    def report(check, ok, detail):
        safe_detail = deps.sanitize(detail)
        print(format_check_line(check, ok, safe_detail, deps.paint))

    return report


def run_doctor(_args, deps):
    conf = deps.read_config()
    api_key, backend = deps.resolve_key(conf)
    api_url = deps.resolve_api_url(conf)
    print(f"ambient {deps.version}  ·  doctor", file=sys.stderr)
    report = _reporter(deps)
    _report_runtime_and_config(bool(api_key), key_backend_label(backend),
                               report, deps)
    models, ready = _check_catalog(api_key, api_url, report, deps)
    ok, category = _check_auth(
        api_key, api_url, models, report, deps)
    _report_models(conf, models, ready, report, deps)
    _report_environment(report, deps)
    if ok:
        print("\nDIAGNOSIS: healthy. Key valid, account billable, API reachable."
              + ("" if ready else " (No models are serving this minute — the "
                 "network scales with demand; retry soon.)"))
        return
    print(f"\nDIAGNOSIS [auth]: {auth_status_detail(False, category)}")
    raise SystemExit(1)


def _report_runtime_and_config(key_present, backend, report, deps):
    interpreter = deps.which("python3")
    if interpreter:
        version = ".".join(str(number) for number in sys.version_info[:3])
        report("runtime", True, f"python3 -> {interpreter} ({version})")
    else:
        report("runtime", False,
               "python3 not on PATH — Codex cannot start the Ambient MCP server. "
               "Install Python 3.8+ (macOS: `xcode-select --install`) and reopen Codex.")
    if os.path.exists(deps.config_path):
        mode = os.stat(deps.config_path).st_mode & 0o777
        report("config", mode == 0o600,
               f"{deps.config_path} (perms {oct(mode)[2:]})")
    else:
        report("config", False,
               f"{deps.config_path} missing — run: {deps.launcher_name} setup")
    _report_key(key_present, backend, report, deps)


def _report_key(key_present, backend, report, deps):
    if key_present:
        hardening = ""
        if backend == "file" and deps.keychain_available():
            hardening = (f" — harden: `{deps.launcher_name} setup --force` "
                         "moves it into the Keychain")
        report("key", True, f"present ({backend}){hardening}")
    else:
        report("key", False, f"MISSING — run: {deps.launcher_name} setup")
    if deps.shared_key_env_is_set():
        report("key isolation", True,
               f"${deps.shared_api_key_env} is exported and IGNORED here (every "
               f"Ambient install reads it) — use ${deps.api_key_env} to override "
               "this install's key")


def _check_catalog(api_key, api_url, report, deps):
    try:
        status, body = deps.api_request(
            api_url, api_key or "none", "/v1/models", timeout=30)
    except deps.network_error:
        report("network", False, "request failed")
        print("\nDIAGNOSIS: cannot reach Ambient at all — check YOUR internet first; "
              "if other sites work, Ambient may be unreachable/down.")
        raise SystemExit(1)
    if status != 200:
        report("service", False, f"Ambient API returned HTTP {status}")
        print(f"\nDIAGNOSIS: Ambient API returned HTTP {status}")
        raise SystemExit(1)
    models = deps.dedupe_catalog(deps.catalog_data(body))
    ready = deps.ready_model_ids(models)
    report("network", True,
           f"api reachable; {len(models)} models, {len(ready)} serving")
    return models, ready


def _check_auth(api_key, api_url, models, report, deps):
    if not api_key:
        print(f"\nDIAGNOSIS: no API key configured. Run: {deps.launcher_name} setup")
        raise SystemExit(1)
    try:
        ok, category, _detail = deps.auth_probe(api_url, api_key, models)
    except deps.network_error:
        report("auth", False, "network request failed")
        print("\nDIAGNOSIS: network dropped mid-check — retry ambient-codex doctor.")
        raise SystemExit(1)
    report("auth+billing", ok, auth_status_detail(ok, category))
    return ok, category


def _report_models(conf, models, ready, report, deps):
    namespace = argparse.Namespace(model=None)
    allow, hide, show, _notes = deps.curation(conf)
    for kind in ("chat", "code"):
        model = deps.resolve_model(namespace, conf, kind)
        if deps.is_auto_model(model):
            report(f"{kind}_model", bool(ready),
                   f"{model} — delegated; resolves per call "
                   f"({len(ready)} model(s) READY right now)")
            continue
        known = any(item.get("id") == model for item in models
                    if isinstance(item, dict))
        hidden_tag = (" (hidden by curation — still works)"
                      if deps.is_hidden(model, allow, hide, show) else "")
        state = _model_state(model, known, model in ready)
        report(f"{kind}_model", known, f"{model} — {state}{hidden_tag}")
    _report_curation(models, allow, hide, show, report, deps)


def _model_state(_model, known, live):
    if live:
        return "READY"
    if known:
        return "not serving at this moment (scales up on demand)"
    return "NOT IN CATALOG — pick again: ambient-codex use"


def _report_curation(models, allow, hide, show, report, deps):
    if not (allow or hide):
        return
    ids = [item.get("id") for item in models
           if isinstance(item, dict) and item.get("id")]
    visible = [model_id for model_id in ids
               if not deps.is_hidden(model_id, allow, hide, show)]
    report("curation", bool(visible) or not ids,
           f"{len(visible)} surfaced / {len(ids) - len(visible)} hidden"
           + ("" if visible or not ids else
              " — the menu is EMPTY; fix: ambient-codex curate reset"))


def _report_environment(report, deps):
    _report_launchers(report, deps)
    report("python", sys.version_info >= (3, 8),
           f"{sys.version.split()[0]}" + (
               "" if sys.version_info >= (3, 10)
               else " (<3.10: stream-timeout diagnoses are less precise)"))
    _report_agent(report, deps)
    _report_cache(report, deps)


def _report_launchers(report, deps):
    launcher = deps.which(deps.launcher_name)
    current = deps.bundled_cli_path()
    if launcher and os.path.realpath(launcher) == current:
        report("launcher", True,
               f"`{deps.launcher_name}` is on your PATH -> this install")
    elif launcher and deps.stable_launcher_is_ours(launcher):
        report("launcher", True,
               f"`{deps.launcher_name}` on PATH is the active Codex launcher")
    elif launcher:
        report("launcher", True,
               f"`{deps.launcher_name}` on PATH points at {launcher} (a different "
               f"ambient-codex install) — refresh with: {deps.launcher_name} link")
    else:
        report("launcher", False,
               f"`{deps.launcher_name}` is not on your PATH — fix: "
               f"{deps.launcher_name} link")
    foreign = deps.which("ambient")
    if foreign and os.path.realpath(foreign) != current:
        report("coexistence", True,
               f"`ambient` on PATH is a different Ambient install ({foreign}); "
               "the two keep separate keys, settings, and usage")


def _report_agent(report, deps):
    if not deps.which("opencode"):
        report("agent", True,
               "opencode not installed (optional — brew install opencode)")
        return
    detail, ok = "opencode installed", True
    try:
        with open(deps.opencode_config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        if deps.opencode_provider in (config.get("provider") or {}):
            detail += f"; {deps.opencode_provider} provider configured"
        else:
            detail += "; provider auto-configures on first `ambient-codex agent`"
    except FileNotFoundError:
        detail += "; config auto-creates on first `ambient-codex agent`"
    except (OSError, json.JSONDecodeError):
        ok = False
        detail = (f"{deps.opencode_config_path} is invalid JSON — "
                  "`ambient-codex agent` will misbehave; fix or delete it")
    report("agent", ok, detail)


def _report_cache(report, deps):
    try:
        count = len([entry for entry in os.listdir(deps.cache_dir)
                     if entry.endswith(".json")])
        mode = os.stat(deps.cache_dir).st_mode & 0o777
        report("cache", mode == 0o700,
               f"{count} entries at {deps.cache_dir} (perms {oct(mode)[2:]})")
    except OSError:
        report("cache", True, "empty (created on first map-reduce)")


__all__ = ("DoctorDependencies", "auth_status_detail", "format_check_line",
           "key_backend_label", "run_doctor")
