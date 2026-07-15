"""Late-bound tool handlers for the native Ambient MCP server."""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List

def status_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, set())
    return deps['run_ambient'](['config'])

def control_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'all_models', 'offline'})
    argv = ['control', '--json']
    if deps['optional_bool'](args, 'all_models', False):
        argv.append('--all-models')
    if deps['optional_bool'](args, 'offline', False):
        argv.append('--offline')
    return deps['_with_session_mode'](deps['run_ambient'](argv))

def _with_session_mode(result: Dict[str, Any], *, deps) -> Dict[str, Any]:
    """Overlay the transient MCP mode on the CLI's persistent control snapshot."""
    if result.get('isError'):
        return result
    try:
        text = result['content'][0]['text']
        payload = json.loads(text)
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    mode = deps['SESSION'].mode
    options = payload.get('mode_options')
    updated_options = [{**option, 'current': option.get('state') == mode} if isinstance(option, dict) else option for option in options] if isinstance(options, list) else options
    return deps['tool_text'](json.dumps({**payload, 'mode': mode, 'mode_options': updated_options}, indent=2))

def set_mode_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'state'})
    state = deps['require_choice'](args, 'state', ('off', 'on', 'takeover'))
    deps['SESSION'].mode = state
    if state == 'takeover':
        return deps['tool_text']('Ambient session is ON. Ambient is now the direct chat and work engine for asks, orchestration, code, builds, and audits; Codex is only the safe local bridge. Use task-specific CLI lanes rather than forcing large work through chat. This Codex session only — turn it off with `ambient_set_mode` state `off`; a fresh Codex session starts in normal mode.')
    if state == 'on':
        return deps['tool_text']('Ambient delegate mode is ON for this Codex session. Ambient handles token-heavy work; normal Codex remains the default. A fresh Codex session starts in normal mode.')
    return deps['tool_text']('Normal Codex mode is ON. Ambient session and delegate routing are off for this Codex session.')

def _mode_menu_text(current: str, reason: str, *, deps) -> str:
    listing = '\n'.join((f'  {i}. {label} — {description}' for i, (_state, label, description) in enumerate(deps['_MODE_OPTIONS'], 1)))
    return f"{reason} Mode unchanged; current mode is '{current}'.\nAvailable modes:\n{listing}\nTo change it, call `ambient_set_mode` with `state` set to `off`, `on`, or `takeover` (Ambient session)."

def pick_mode_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    """Render a native picker for the session-only Ambient operating mode.

    Mirrors `ambient_pick_model`: a tap-to-choose session mode picker, with a
    numbered text menu fallback for clients without elicitation and headless runs.
    """
    deps['reject_unknown'](args, set())
    current = deps['SESSION'].mode
    if not deps['SESSION'].supports_elicitation():
        return deps['tool_text'](deps['_mode_menu_text'](current, 'This client cannot render a native mode picker.'))
    schema = {'type': 'object', 'properties': {'state': {'type': 'string', 'title': 'Ambient mode', 'description': f'Currently: {current}', 'enum': [state for state, _label, _description in deps['_MODE_OPTIONS']], 'enumNames': [label for _state, label, _description in deps['_MODE_OPTIONS']]}}, 'required': ['state']}
    result = deps['elicit']('Choose how much work Codex routes to Ambient', schema)
    chosen = deps['elicitation_choice'](result, 'state')
    if not chosen:
        return deps['tool_text'](deps['_mode_menu_text'](current, 'No mode was selected; the picker may have been cancelled or unavailable.'))
    if chosen not in {state for state, _label, _description in deps['_MODE_OPTIONS']}:
        return deps['tool_text'](f'Mode unchanged — {chosen!r} is not a valid mode.', is_error=True)
    return deps['set_mode_tool']({'state': chosen})

def set_model_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'model', 'lane'})
    model = deps['require_string'](args, 'model', max_chars=256)
    lane = deps['require_choice'](args, 'lane', ('both', 'chat', 'code'))
    argv = ['control', 'model']
    if lane == 'chat':
        argv.append('--chat')
    elif lane == 'code':
        argv.append('--code')
    argv.extend(['--', model])
    return deps['run_ambient'](argv)

def _serving_models(*, deps) -> List[Dict[str, Any]]:
    """Models the network is serving right now, as `ambient models --json` sees them.

    A model that is not serving this minute is normal on a decentralized network, so
    the picker offers only what can answer immediately rather than a stale catalogue.
    """
    command = [sys.executable, str(deps['ambient_bin']()), 'models', '--json']
    try:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(deps['plugin_root']()), timeout=deps['DEFAULT_TIMEOUT_SECONDS'], check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise deps['AmbientCommandError'](f'unable to list Ambient models: {exc}') from exc
    if completed.returncode != 0:
        raise deps['AmbientCommandError'](deps['redact'](completed.stderr.strip() or 'ambient models failed'))
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise deps['AmbientCommandError'](f'ambient models returned non-JSON: {exc}') from exc
    models = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    serving = [m for m in models if isinstance(m, dict) and m.get('ready') and (not m.get('hidden')) and m.get('id')]
    return serving[:deps['MAX_PICKER_OPTIONS']]

def _model_label(model: Dict[str, Any], *, deps) -> str:
    label = str(model.get('id', ''))
    note = model.get('note') or model.get('description')
    if isinstance(note, str) and note.strip():
        label = f'{label} — {note.strip()}'
    return label[:120]

def _model_menu_text(serving: List[Dict[str, Any]], reason: str, *, deps) -> str:
    listing = '\n'.join((f"  {i}. {deps['_model_label'](model)}" for i, model in enumerate(serving, 1)))
    browse = len(serving) + 1
    return f'{reason} Model unchanged.\nServing now - ready for immediate use:\n{listing}\n  {browse}. Browse all models - includes on-demand models that may take longer to start.\nTo change it, call `ambient_set_model` with the selected model id. To browse everything first, call `ambient_models` with `all=true`; show ready models first and label the rest as on-demand.'

def pick_model_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    """Render a native Codex picker for the model + lane, then persist the choice.

    Falls back to a numbered text menu whenever a picker cannot be drawn: an older
    client, a client that never advertised elicitation, or a headless `codex exec`
    run (where Codex auto-cancels elicitations because no human is present).
    """
    deps['reject_unknown'](args, {'lane'})
    lane = deps['require_choice'](args, 'lane', ('both', 'chat', 'code')) if 'lane' in args else 'both'
    serving = deps['_serving_models']()
    if not serving:
        return deps['tool_text']('No Ambient models are serving this minute. That does not mean the catalog is broken: Ambient models can spin up on demand. Call `ambient_models` with `all=true` to browse all models, then call `ambient_set_model` with the selected model id.')
    if not deps['SESSION'].supports_elicitation():
        return deps['tool_text'](deps['_model_menu_text'](serving, 'This client cannot render a native model picker.'))
    lane_label = {'both': 'chat + code', 'chat': 'chat', 'code': 'code'}[lane]
    schema = {'type': 'object', 'properties': {'model': {'type': 'string', 'title': 'Ambient model', 'description': 'Serving now on Ambient; browse all models separately for on-demand options', 'enum': [str(m['id']) for m in serving], 'enumNames': [deps['_model_label'](m) for m in serving]}}, 'required': ['model']}
    result = deps['elicit'](f'Select the Ambient model for {lane_label}', schema)
    chosen = deps['elicitation_choice'](result, 'model')
    if not chosen:
        return deps['tool_text'](deps['_model_menu_text'](serving, 'No model was selected; the picker may have been cancelled or unavailable.'))
    offered = {str(m['id']) for m in serving}
    if chosen not in offered:
        return deps['tool_text'](f'Model unchanged — {chosen!r} was not one of the offered models.', is_error=True)
    argv = ['control', 'model', chosen]
    if lane == 'chat':
        argv.append('--chat')
    elif lane == 'code':
        argv.append('--code')
    return deps['run_ambient'](argv)

def set_config_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'name', 'value', 'unset'})
    name = deps['require_choice'](args, 'name', ('streaming', 'fallback', 'savings'))
    unset = deps['optional_bool'](args, 'unset', False)
    value = deps['optional_string'](args, 'value', max_chars=128)
    if unset and value is not None:
        raise deps['ToolInputError']('value cannot be provided when unset=true')
    if unset:
        return deps['run_ambient'](['control', 'setting', name, '--unset'])
    if value is None:
        raise deps['ToolInputError']('value is required unless unset=true')
    return deps['run_ambient'](['control', 'setting', name, '--', value])

def key_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'action'})
    action = deps['require_choice'](args, 'action', ('status', 'setup', 'rotate', 'remove'))
    return deps['run_ambient'](['control', 'key', action])

def models_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'all', 'json'})
    argv = ['models']
    if deps['optional_bool'](args, 'all', False):
        argv.append('--all')
    if deps['optional_bool'](args, 'json', True):
        argv.append('--json')
    return deps['run_ambient'](argv)

def doctor_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, set())
    return deps['run_ambient'](['doctor'])

def usage_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'days', 'json'})
    days = deps['optional_int'](args, 'days', 30, minimum=1, maximum=3650)
    argv = ['usage', '--days', str(days)]
    if deps['optional_bool'](args, 'json', True):
        argv.append('--json')
    return deps['run_ambient'](argv)

def self_test_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, set())
    root = deps['plugin_root']()
    binary = deps['ambient_bin']()
    if not root.is_dir():
        return deps['tool_text'](f'ambient-codex self-test failed: plugin root missing: {root}', is_error=True)
    if not binary.is_file():
        return deps['tool_text'](f"ambient-codex self-test failed: {deps['missing_cli_message'](root, binary)}", is_error=True)
    env = {name: value for name, value in os.environ.items() if name != 'AMBIENT_API_KEY'}
    try:
        completed = subprocess.run([sys.executable, str(binary), 'version'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(root), timeout=deps['SELF_TEST_TIMEOUT_SECONDS'], check=False, env=env)
    except OSError as exc:
        return deps['tool_text'](f'ambient-codex self-test failed: unable to launch bundled CLI: {exc}', is_error=True)
    except subprocess.TimeoutExpired:
        return deps['tool_text'](f"ambient-codex self-test failed: bundled CLI version timed out after {deps['SELF_TEST_TIMEOUT_SECONDS']}s", is_error=True)
    if completed.returncode:
        details = '\n'.join((part for part in (completed.stdout.strip(), completed.stderr.strip()) if part))
        return deps['tool_text'](f'ambient-codex self-test failed: bundled CLI exited {completed.returncode}\n{details}', is_error=True)
    payload = {'schema_version': 1, 'status': 'ok', 'message': 'ambient-codex self-test ok', 'server': deps['SERVER_NAME'], 'server_version': deps['SERVER_VERSION'], 'plugin_root': str(root), 'ambient_version': completed.stdout.strip()}
    return deps['tool_text'](json.dumps(payload, indent=2))

def ask_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'prompt', 'system', 'model', 'max_tokens', 'timeout', 'json'})
    prompt = deps['require_string'](args, 'prompt', max_chars=deps['MAX_PROMPT_CHARS'])
    system = deps['optional_string'](args, 'system', max_chars=deps['MAX_SYSTEM_CHARS'])
    model = deps['optional_string'](args, 'model', max_chars=256)
    max_tokens = args.get('max_tokens')
    if max_tokens is not None:
        deps['optional_int'](args, 'max_tokens', 0, minimum=1, maximum=1000000)
    timeout_seconds = deps['optional_int'](args, 'timeout', deps['DEFAULT_TIMEOUT_SECONDS'], minimum=1, maximum=3600)
    argv = ['ask', '--yes']
    if system is not None:
        argv.append(f'--system={system}')
    if model is not None:
        argv.append(f'--model={model}')
    if max_tokens is not None:
        argv.extend(['--max-tokens', str(max_tokens)])
    if deps['optional_bool'](args, 'json', True):
        argv.append('--json')
    argv.extend(['--', prompt])
    return deps['compact_ask_result'](deps['run_ambient'](argv, timeout_seconds=timeout_seconds))

def audit_small_tool(args: Dict[str, Any], *, deps) -> Dict[str, Any]:
    deps['reject_unknown'](args, {'paths', 'staged', 'diff', 'focus', 'cwd', 'json', 'timeout'})
    paths = deps['optional_string_list'](args, 'paths', max_items=deps['MAX_PATHS'], max_chars=1000)
    staged = deps['optional_bool'](args, 'staged', False)
    diff = deps['optional_string'](args, 'diff', max_chars=200)
    focus = deps['optional_string'](args, 'focus', max_chars=300)
    cwd_value = deps['optional_string'](args, 'cwd', max_chars=4096)
    timeout_seconds = deps['optional_int'](args, 'timeout', deps['DEFAULT_TIMEOUT_SECONDS'], minimum=1, maximum=3600)
    if not paths and (not staged) and (diff is None):
        raise deps['ToolInputError']('ambient_audit_small requires paths, staged=true, or diff')
    workdir = Path(cwd_value).expanduser().resolve() if cwd_value else Path.cwd()
    if not workdir.is_dir():
        raise deps['ToolInputError']('cwd must be an existing directory')
    deps['reject_oversized_audit_paths'](workdir, paths)
    argv = ['audit']
    if staged:
        argv.append('--staged')
    if diff is not None:
        argv.append(f'--diff={diff}')
    if focus is not None:
        argv.append(f'--focus={focus}')
    if deps['optional_bool'](args, 'json', True):
        argv.append('--json')
    argv.append('--yes')
    if paths:
        argv.extend(['--', *paths])
    return deps['run_ambient'](argv, cwd=workdir, timeout_seconds=timeout_seconds)

_IMPL = {'status_tool': status_tool, 'control_tool': control_tool, '_with_session_mode': _with_session_mode, 'set_mode_tool': set_mode_tool, '_mode_menu_text': _mode_menu_text, 'pick_mode_tool': pick_mode_tool, 'set_model_tool': set_model_tool, '_serving_models': _serving_models, '_model_label': _model_label, '_model_menu_text': _model_menu_text, 'pick_model_tool': pick_model_tool, 'set_config_tool': set_config_tool, 'key_tool': key_tool, 'models_tool': models_tool, 'doctor_tool': doctor_tool, 'usage_tool': usage_tool, 'self_test_tool': self_test_tool, 'ask_tool': ask_tool, 'audit_small_tool': audit_small_tool}

def build(namespace, specification):
    """Build handlers over a read-only, live view of the MCP facade namespace."""
    deps = MappingProxyType(namespace)
    adapters = []
    for name in specification.split():
        implementation = _IMPL.get(name)
        if implementation is None:
            raise ValueError(f"unknown MCP tool handler: {name}")
        def adapter(*args, _implementation=implementation, **kwargs):
            return _implementation(*args, deps=deps, **kwargs)
        adapters.append(functools.update_wrapper(adapter, implementation))
    return tuple(adapters)

__all__ = ("build",)
