"""Parser configuration, dispatch, and process boundary for Ambient CLI."""

import functools
from types import MappingProxyType

def add_common_flags(parser, *, deps):
    return deps['_cli_parser'].add_common_flags(parser, default_timeout_s=deps['DEFAULT_TIMEOUT_S'], max_parallel_chunks=deps['MAX_PARALLEL_CHUNKS'])

def _add_best_of_flag(parser, *, deps):
    return deps['_cli_parser'].add_best_of_flag(parser, best_of_max=deps['BEST_OF_MAX'])

def _configure_version(sub, *, deps):
    return deps['_cli_parser'].configure_version(sub)

def _configure_models(sub, *, deps):
    return deps['_cli_parser'].configure_models(sub)

def _configure_curate(sub, *, deps):
    return deps['_cli_parser'].configure_curate(sub)

def _configure_setup(sub, *, deps):
    return deps['_cli_parser'].configure_setup(sub)

def _configure_link(sub, *, deps):
    return deps['_cli_parser'].configure_link(sub)

def _configure_uninstall(sub, *, deps):
    return deps['_cli_parser'].configure_uninstall(sub, state_dir=deps['STATE_DIR'])

def cmd_uninstall(args, *, deps):
    """Cleanly offboard Ambient Codex. Touches ONLY this install's own resources —
    the ambient-codex keychain item, ~/.config/ambient-codex, and the ambient-codex
    launcher — never another Ambient install's key, state, launcher, or git hooks.

    Removing the plugin itself is a Codex action, so we print the command rather than
    run it."""
    return deps['_maintenance_commands'].run_uninstall(args, deps['_maintenance_commands'].UninstallDependencies(state_dir=deps['STATE_DIR'], foreign_root=deps['foreign_root'], keychain_delete=deps['keychain_delete'], keychain_read=deps['keychain_read'], save_config=deps['save_config_values'], command_link=deps['cmd_link'], launcher_name=deps['LAUNCHER_NAME'], keychain_service=deps['KEYCHAIN_SERVICE']))

def _configure_cache(sub, *, deps):
    return deps['_cli_parser'].configure_cache(sub)

def _configure_trust_url(sub, *, deps):
    return deps['_cli_parser'].configure_trust_url(sub)

def _configure_usage(sub, *, deps):
    return deps['_cli_parser'].configure_usage(sub)

def _configure_mode(sub, *, deps):
    return deps['_cli_parser'].configure_mode(sub)

def _configure_config(sub, *, deps):
    return deps['_cli_parser'].configure_config(sub)

def _configure_control(sub, *, deps):
    return deps['_cli_parser'].configure_control(sub, parser_class=deps['_Parser'])

def _configure_doctor(sub, *, deps):
    return deps['_cli_parser'].configure_doctor(sub)

def _configure_use(sub, *, deps):
    return deps['_cli_parser'].configure_use(sub)

def _configure_ask(sub, *, deps):
    return deps['_cli_parser'].configure_ask(sub, add_common=deps['add_common_flags'], add_best_of=deps['_add_best_of_flag'])

def _configure_audit(sub, *, deps):
    return deps['_cli_parser'].configure_audit(sub, add_common=deps['add_common_flags'], add_best_of=deps['_add_best_of_flag'])

def _configure_map(sub, *, deps):
    return deps['_cli_parser'].configure_map(sub, add_common=deps['add_common_flags'])

def _configure_code(sub, *, deps):
    return deps['_cli_parser'].configure_code(sub, add_common=deps['add_common_flags'], add_best_of=deps['_add_best_of_flag'])

def _configure_chat(sub, *, deps):
    return deps['_cli_parser'].configure_chat(sub, add_common=deps['add_common_flags'])

def _configure_build(sub, *, deps):
    return deps['_cli_parser'].configure_build(sub, add_common=deps['add_common_flags'])

def _configure_agent(sub, *, deps):
    return deps['_cli_parser'].configure_agent(sub)

def _configure_codex(sub, *, deps):
    return deps['_cli_parser'].configure_codex(sub)

def _cmd_version(args, *, deps):
    print(f"ambient {deps['__version__']}")

def _cmd_models_keyless(args, *, deps):
    conf = deps['read_config_file']()
    key, _ = deps['resolve_key_and_backend'](conf)
    try:
        deps['cmd_models'](args, key or 'none', deps['resolve_api_url'](conf), conf)
    except deps['NetworkError'] as err:
        deps['sys'].exit(f'ambient [network]: {err}')

def _cmd_trust_url_dispatch(args, *, deps):
    if args.reset:
        deps['cmd_trust_url_reset']()
    elif args.url:
        deps['cmd_trust_url'](args)
    else:
        deps['usage_exit']('trust-url needs a URL (or --reset)')

def _cmd_codex_keyless(args, *, deps):
    deps['cmd_codex'](args, None, None, None)

def _audit_keyless_route(args, *, deps):
    """audit --install-hook / --uninstall-hook is pure hooks-file management
    (7d): no key, no network — it must work before any credentials exist."""
    if getattr(args, 'install_hook', None) or getattr(args, 'uninstall_hook', None):
        return 'cmd_audit_hook'
    return None

def _registry_handler(name, *, deps):
    """Resolve a registry handler by NAME at dispatch time, so tests that
    monkeypatch module attributes (amb.cmd_ask = stub) keep working."""
    return deps[name]

def build_parser(*, deps):
    """Construct the fully-configured top-level parser (extracted so tests and
    main share one definition)."""
    return deps['_cli_dispatch'].build_parser(parser_class=deps['_Parser'], description=__doc__, version=deps['__version__'], commands=deps['COMMANDS'])

def _parse_args_with_stdin_dash(parser, *, deps):
    """parse_args, but rescue a lone stdin sentinel '-' that argparse's greedy
    nargs='*' positional orphans when it TRAILS a value option — e.g.
    `ask "prompt" -m MODEL -`, the most natural order, which otherwise errors
    with 'unrecognized arguments: -' (stress test F03). For `ask` the '-' is
    injected into the prompt (its handler reads stdin as extra context); for
    every other command a lone trailing '-' is simply DROPPED — audit/map
    already auto-read piped stdin, and code takes no stdin — so the natural
    order never errors on any command (Codex round 2). Any OTHER leftover is
    still a real usage error, exactly like parse_args."""
    return deps['_cli_dispatch'].parse_args_with_stdin_dash(parser)

def main(*, deps):
    parser = deps['build_parser']()
    if len(deps['sys'].argv) > 2 and deps['sys'].argv[1] in ('setup', 'config', 'control'):
        _keyname = ('key', 'api-key', 'apikey', 'ambient-api-key')
        for tok in deps['sys'].argv[2:]:
            if '=' in tok and tok.lstrip('-').split('=', 1)[0].strip().lower().replace('_', '-') in _keyname:
                deps['sys'].exit(f"ambient: don't pass an API key on the command line — it lands in your shell history. Run `{deps['LAUNCHER_NAME']} setup` (interactive, hidden input) instead, and ROTATE that key at {deps['KEY_CONSOLE_URL']}.")
            if tok.startswith('-'):
                if '=' not in tok:
                    continue
                tok = tok.split('=', 1)[1]
            if len(tok) >= 20 and deps['re'].fullmatch('[A-Za-z0-9_.\\-]{20,}', tok) or any((p.search(tok) for p in deps['SECRET_PATTERNS'])):
                deps['sys'].exit(f"ambient: it looks like you passed your API key on the command line — it is now in your shell history. Run `{deps['LAUNCHER_NAME']} setup` (interactive, hidden input) instead, and ROTATE that key at {deps['KEY_CONSOLE_URL']}.")
    args = deps['_parse_args_with_stdin_dash'](parser)
    spec = deps['_cli_dispatch'].find_command(deps['COMMANDS'], args.command)
    if spec is not None and spec.get('pre_env'):
        deps['_registry_handler'](spec['handler'])(args)
        return
    if args.command is None:
        print(deps['build_banner']())
        return
    if deps['CONFIG_PATH'].startswith('~'):
        deps['_fail_exit'](args, deps['_argv_command'](), 'config', 'cannot resolve your home directory (HOME unset). Set HOME and retry.')
    if getattr(args, 'max_tokens', None) is not None:
        if args.max_tokens < deps['MIN_OUTPUT_TOKENS']:
            print(f"ambient: --max-tokens {args.max_tokens} is too low for a reasoning model (it needs room to think AND answer) — raising to {deps['MIN_OUTPUT_TOKENS']}", file=deps['sys'].stderr)
            args.max_tokens = deps['MIN_OUTPUT_TOKENS']
        else:
            args.max_tokens = deps['normalize_requested_max_tokens'](args.max_tokens)
    if getattr(args, 'temperature', None) is not None:
        t = args.temperature
        if t != t or not 0.0 <= t <= 2.0:
            fixed = 0.1 if t != t else max(0.0, min(2.0, t))
            print(f'ambient: --temperature {t} is invalid — using {fixed}', file=deps['sys'].stderr)
            args.temperature = fixed
    if getattr(args, 'timeout', None) is not None and args.timeout <= 0:
        print(f"ambient: --timeout must be positive — using {deps['DEFAULT_TIMEOUT_S']}s", file=deps['sys'].stderr)
        args.timeout = deps['DEFAULT_TIMEOUT_S']
    if not spec['needs_key']:
        deps['_registry_handler'](spec['handler'])(args)
        return
    route = spec.get('keyless_route')
    if route:
        alt = deps['_registry_handler'](route)(args)
        if alt:
            deps['_registry_handler'](alt)(args)
            return
    api_key, api_url, conf = deps['load_config']()
    deps['_PROGRESS_DISPLAY']['resolved'] = deps['_resolve_progress_display'](args, conf)
    try:
        deps['_registry_handler'](spec['handler'])(args, api_key, api_url, conf)
    except deps['ChatError'] as err:
        if deps['_json_mode'](args):
            deps['emit_json_error'](args.command, err.category, err.diagnosis, api_key)
        deps['sys'].exit(f'ambient [{err.category}]: {err.diagnosis}')
    except deps['NetworkError'] as err:
        if deps['_json_mode'](args):
            deps['emit_json_error'](args.command, 'network', str(err), api_key)
        deps['sys'].exit(f'ambient [network]: {err}. Check your internet connection; if other sites work, Ambient may be unreachable — run: ambient-codex doctor')

def shielded_main(*, deps):
    """No raw traceback ever reaches the user: every unexpected failure becomes
    a clean, redacted, one-line diagnosis. AMBIENT_DEBUG=1 restores tracebacks."""
    for _stream in (deps['sys'].stdout, deps['sys'].stderr):
        try:
            _stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError, OSError):
            pass
    try:
        deps['main']()
    except (KeyboardInterrupt, EOFError):
        deps['sys'].stderr.write('\nambient: cancelled\n')
        deps['sys'].exit(130)
    except SystemExit:
        raise
    except deps['NetworkError'] as err:
        msg = f'{err}. Check your internet connection; if other sites work, Ambient may be unreachable — run: ambient-codex doctor'
        deps['_fail_exit'](None, deps['_argv_command'](), 'network', msg, prose=f'ambient [network]: {msg}')
    except Exception as err:
        if deps['os'].environ.get('AMBIENT_DEBUG') == '1':
            raise
        key = ''
        try:
            key = deps['resolve_key_and_backend'](deps['read_config_file']())[0] or ''
        except Exception:
            pass
        detail = deps['redact'](f'{type(err).__name__}: {err}', key)[:300]
        msg = f"unexpected error ({detail}). Nothing was harmed. Run 'ambient-codex doctor' to check the basics; set AMBIENT_DEBUG=1 to see full details."
        deps['_fail_exit'](None, deps['_argv_command'](), 'internal', msg, api_key=key, prose=f'ambient [internal]: {msg}')

_IMPL = {'add_common_flags': add_common_flags, '_add_best_of_flag': _add_best_of_flag, '_configure_version': _configure_version, '_configure_models': _configure_models, '_configure_curate': _configure_curate, '_configure_setup': _configure_setup, '_configure_link': _configure_link, '_configure_uninstall': _configure_uninstall, 'cmd_uninstall': cmd_uninstall, '_configure_cache': _configure_cache, '_configure_trust_url': _configure_trust_url, '_configure_usage': _configure_usage, '_configure_mode': _configure_mode, '_configure_config': _configure_config, '_configure_control': _configure_control, '_configure_doctor': _configure_doctor, '_configure_use': _configure_use, '_configure_ask': _configure_ask, '_configure_audit': _configure_audit, '_configure_map': _configure_map, '_configure_code': _configure_code, '_configure_chat': _configure_chat, '_configure_build': _configure_build, '_configure_agent': _configure_agent, '_configure_codex': _configure_codex, '_cmd_version': _cmd_version, '_cmd_models_keyless': _cmd_models_keyless, '_cmd_trust_url_dispatch': _cmd_trust_url_dispatch, '_cmd_codex_keyless': _cmd_codex_keyless, '_audit_keyless_route': _audit_keyless_route, '_registry_handler': _registry_handler, 'build_parser': build_parser, '_parse_args_with_stdin_dash': _parse_args_with_stdin_dash, 'main': main, 'shielded_main': shielded_main}

def build(namespace, specification):
    """Build entrypoint adapters over a read-only live facade namespace."""
    deps = MappingProxyType(namespace)
    adapters = []
    for item in specification.split():
        public, separator, target = item.partition("=")
        target = target if separator else public
        implementation = _IMPL.get(target)
        if not public.isidentifier() or implementation is None:
            raise ValueError(f"unknown CLI entrypoint adapter: {item}")
        def adapter(*args, _implementation=implementation, **kwargs):
            return _implementation(*args, deps=deps, **kwargs)
        adapter = functools.update_wrapper(adapter, implementation)
        adapter.__name__ = public
        adapter.__qualname__ = public
        adapters.append(adapter)
    return tuple(adapters)

__all__ = ("build",)
