"""External OpenCode provider configuration and process handoff."""

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDependencies:
    provider: str
    ensure_config: object
    resolve_model: object
    note_if_hidden: object
    is_auto_model: object
    resolve_auto_model: object
    safe_catalog: object
    build_argv: object
    which: object
    run_process: object


def ensure_provider_config(path, provider, api_url, model, update_provider):
    config = _read_provider_config(path)
    if config is None:
        return
    updated = update_provider(
        config, provider=provider, api_url=api_url, model=model)
    if updated is None:
        print(f"ambient: {path} provider section is not a JSON object — leaving "
              "it alone; `ambient-codex agent` may misbehave "
              "(see: ambient-codex doctor)", file=sys.stderr)
        return
    if updated == config:
        return
    _write_provider_config(path, updated)


def _read_provider_config(path):
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        print(f"ambient: {path} could not be parsed — leaving it alone; "
              "`ambient-codex agent` may misbehave (see: ambient-codex doctor)",
              file=sys.stderr)
        return None
    if isinstance(config, dict):
        return config
    print(f"ambient: {path} is not a JSON object — leaving it alone; "
          "`ambient-codex agent` may misbehave (see: ambient-codex doctor)",
          file=sys.stderr)
    return None


def _write_provider_config(path, config):
    temporary = None
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        try:
            original_mode = os.stat(path).st_mode & 0o777
        except OSError:
            original_mode = 0o600
        temporary = path + f".tmp-{os.getpid()}-{time.time_ns()}"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, original_mode)
        print(f"ambient: wrote opencode provider config → {path}",
              file=sys.stderr)
    except OSError as error:
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
        print(f"ambient: could not write opencode config ({error})",
              file=sys.stderr)


def run_agent(args, api_key, api_url, conf, deps):
    executable = deps.which("opencode")
    if not executable:
        raise SystemExit(
            "ambient: opencode not found on PATH. Install it with:\n"
            "  brew install opencode      (or see https://opencode.ai)")
    model = deps.resolve_model(args, conf, "code")
    if getattr(args, "model", None):
        deps.note_if_hidden(args.model, conf)
    if deps.is_auto_model(model):
        model = deps.resolve_auto_model(
            model, deps.safe_catalog(api_url, api_key), conf, 0, args)
    deps.ensure_config(api_url, model)
    print("ambient: the agent reads files itself — the secrets tripwire does "
          "NOT cover this lane; keep .env/credentials out of its working tree.",
          file=sys.stderr)
    environment = {**os.environ, "AMBIENT_CODEX_API_KEY": api_key}
    argv = deps.build_argv(args.agent_args, provider=deps.provider, model=model)
    if os.name == "nt":
        _run_windows_agent(executable, argv, environment, deps.run_process)
        return
    try:
        os.execvpe("opencode", argv, environment)
    except FileNotFoundError:
        raise SystemExit(
            "ambient: opencode not found on PATH (brew install opencode)")


def _run_windows_agent(executable, argv, environment, run_process):
    try:
        process = run_process([executable or "opencode", *argv[1:]],
                              env=environment)
    except FileNotFoundError:
        raise SystemExit(
            "ambient: opencode not found on PATH (see https://opencode.ai)")
    raise SystemExit(process.returncode)


def run_codex():
    raise SystemExit(
        "ambient: Codex CLI cannot talk to Ambient yet — Codex >=0.142 only speaks "
        "the OpenAI Responses API, and Ambient's /v1/responses gateway rejects "
        "Codex's 'namespace' tool type and typed message content (verified "
        "2026-07-02). The provider config is already in place in ~/.codex; once "
        "Ambient's validator accepts/ignores unknown tool types this will work.\n"
        "Use the working agentic terminal instead:  ambient-codex agent")


__all__ = ("AgentDependencies", "ensure_provider_config", "run_agent",
           "run_codex")
