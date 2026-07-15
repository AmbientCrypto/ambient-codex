"""Late-bound API client, usage, session, and completion facade adapters."""

import functools
from types import MappingProxyType

def error_message(body, *, deps):
    return deps['_response_errors'].error_message(body)

def classify_error(status, body, api_key, *, deps):
    """Map an API failure to (category, user-facing diagnosis) so users can tell
    a dead key from an empty account from a busy model from a real outage.
    Categories: key | funds | model | rate | service | unknown."""
    return deps['_response_errors'].classify_error(status, body, api_key, deps['redact'], deps['LAUNCHER_NAME'])

def auth_probe(api_url, api_key, models, *, deps):
    """Verify a key with a minimal real completion (the models endpoint is
    unauthenticated, so it proves nothing about the key). Returns
    (ok, category, detail). A not-serving 429 still proves auth passed."""
    ready = deps['ready_model_ids'](models)
    probe_model = ready[0] if ready else next((m['id'] for m in models if isinstance(m, dict) and isinstance(m.get('id'), str)), deps['DEFAULT_MODEL'])
    payload = {'model': probe_model, 'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 16}
    status, body = deps['api_request'](api_url, api_key, '/v1/chat/completions', payload, timeout=60)
    if status == 200:
        return (True, 'ok', f"live completion on {deps['sanitize'](probe_model)} succeeded")
    category, detail = deps['classify_error'](status, body, api_key)
    if category == 'model':
        return (True, category, "authentication passed (verified even while the probe model isn't serving — that response only comes after a valid key)")
    if category in ('rate', 'service'):
        return (False, category, f'could not verify the key ({detail}) — retry in a minute')
    return (False, category, detail)

def refuse_if_secrets(labeled_chunks, allow, *, deps):
    """Refuse likely credentials while reporting bounded locations, never content."""
    if allow:
        return
    hits = deps['_secrets_core'].secret_hits(labeled_chunks)
    if hits:
        msg = f"refusing to send — content that looks like credentials at: {', '.join(hits)}. Redact those lines, or pass --allow-secrets if they are false positives (e.g. variable names)."
        deps['_fail_exit'](None, deps['_argv_command'](), 'secrets', msg, prose=f'ambient [secrets]: {msg}')

def _retry_delay(base, headers=None, *, deps):
    """Backoff with random jitter, honoring a Retry-After header (clamped to 60s)
    when present. Jitter breaks the lockstep that makes 3 fan-out workers
    re-hit the limiter together (thundering herd)."""
    hint = 0.0
    if headers is not None:
        ra = headers.get('Retry-After')
        if ra:
            try:
                hint = float(ra)
            except (TypeError, ValueError):
                hint = 0.0
    delay = min(max(float(base), hint), 60.0)
    return delay + deps['random'].uniform(0, 0.5 * delay)

def _cache_key(model, system, chunk, max_tokens, temperature, response_format=None, salt=None, *, deps):
    """Content address for one chunk call. MUST include model + max_tokens
    (per-model budgets differ) AND response_format — a strict-schema model shares
    the same system prompt for prose vs --json, so omitting it would serve cached
    prose into the JSON reducer or vice-versa. `salt`
 namespaces otherwise-identical calls — each --best-of sample
    index gets its OWN cache entry so re-runs resume per sample; salt=None is
    byte-identical to the pre-salt key."""
    return deps['_cache_store'].cache_key(model, system, chunk, max_tokens, temperature, response_format, salt)

def _cache_get(key, ttl, *, deps):
    return deps['_cache_store'].cache_get(deps['CACHE_DIR'], key, ttl)

def _cache_put(key, text, *, deps):
    deps['_cache_store'].cache_put(deps['CACHE_DIR'], key, text, deps['CACHE_MAX_FILES'], deps['_private_dir'])

def log_usage(model, usage, input_chars=None, *, deps):
    """Best-effort local metering (Ambient has no balance endpoint). Builds the
    enriched ledger record here — token counts, observed char telemetry, the
    priced cost, and the frontier reference in force at call time — then
    delegates fail-open, lock-serialized persistence to
    ``ambient_codex.usage_store``. A skipped trim only lets the ledger grow a
    little; a lost line loses spend signal, so the store spools rather than
    dropping a line under lock contention."""
    try:
        record = {'ts': int(deps['time'].time()), 'model': model, 'in': usage.get('prompt_tokens', 0), 'out': usage.get('completion_tokens', 0)}
        if input_chars and (not usage.get('_estimated')) and isinstance(input_chars, (int, float)) and isinstance(record['in'], (int, float)) and (record['in'] > 0):
            record['chars'] = int(input_chars)
        try:
            if usage.get('_estimated'):
                record['est'] = True
            cost, assumed = deps['usage_cost'](model, usage)
            if not assumed:
                record['cost'] = cost
            ref = deps['resolve_reference_price']()
            record['ref'] = [ref[0], ref[1]]
        except Exception:
            pass
        line = deps['json'].dumps(record) + '\n'
        deps['_usage_store'].append_line(line, usage_path=deps['USAGE_PATH'], max_bytes=deps['USAGE_MAX_BYTES'], trim_keep_lines=deps['USAGE_TRIM_KEEP_LINES'], lock_wait_s=deps['_LEDGER_LOCK_WAIT_S'], private_dir=deps['_private_dir'], fs_lock=deps['_fs_lock'], pid_alive=deps['_pid_alive'])
    except Exception:
        pass

def usage_exit(msg, *, deps):
    """Semantic usage errors share argparse's exit code (EX_USAGE=64) so an
    agentic caller can tell 'wrong invocation' from 'runtime failure'. Under
    --json the same error is ALSO emitted as a parseable envelope (exit 64)."""
    if deps['_json_in_argv']():
        deps['emit_json_error']('usage', 'usage', msg, exit_code=deps['EXIT_USAGE'])
    print(f'ambient: {msg}', file=deps['sys'].stderr)
    deps['sys'].exit(deps['EXIT_USAGE'])

def stream_completion(api_url, api_key, payload, timeout, on_delta=None, *, deps):
    """Facade seam for the extracted dependency-injected SSE transport."""
    return deps['_streaming'].stream_completion(api_url, api_key, payload, timeout, on_delta, opener=deps['urllib'].request.urlopen, network_error=deps['NetworkError'], stall_error=deps['StallError'], stream_line_max=deps['STREAM_LINE_MAX'], heartbeat_s=deps['HEARTBEAT_S'], hard_wall_s=deps['HARD_WALL_S'], noprogress_s=deps['NOPROGRESS_S'], progress_enabled=deps['progress_display_enabled'], stderr=deps['sys'].stderr, stderr_is_tty=deps['_stderr_is_tty'])

def _session_or(session, api_key, api_url, conf=None, *, deps):
    """Normalize the loose (api_key, api_url[, conf]) triple to ONE Session.
    Engine functions accept either form: callers
    that already hold a Session pass it through; legacy positional callers
    get an equivalent Session built on the spot."""
    if session is not None:
        return session
    return deps['Session'](api_url=api_url, api_key=api_key, conf=conf or {})

def _completion_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_completion_command'].CompletionDependencies.bind, 'AttemptState CHARS_PER_TOKEN ChatError DEFAULT_BUDGET_ESCALATIONS MAX_COMPLETE_ATTEMPTS MIN_OUTPUT_TOKENS NetworkError RequestSpec StallError _as_pos_int _budget_escalation_limit _effective_cpt _fallback_enabled _reasoning_str _session_or classify_error fetch_models log_usage model_profile pick_fallback_model read_config_file redact stream_completion')

def complete(api_key, api_url, model, messages, args, _stall_retried=False, _budget_retried=False, _fallback_retried=False, _budget_shrunk=False, on_delta=None, session=None, *, deps):
    return deps['_completion_command'].run_completion(api_key, api_url, model, messages, args, _stall_retried, _budget_retried, _fallback_retried, _budget_shrunk, on_delta, session, deps['_completion_dependencies']())

def chat(api_key, api_url, model, messages, args, kind='ask', session=None, *, deps):
    """Single-shot completion printed to stdout (CLI convenience wrapper).
    Live-streams to an interactive terminal."""
    return deps['_chat_workflow'].single_shot_response(api_key, api_url, model, messages, args, kind=kind, session=session, session_or=deps['_session_or'], request_spec=deps['RequestSpec'].from_args, stdout=deps['sys'].stdout, stream_redactor=deps['_StreamRedactor'], complete=deps['complete'], failure=deps['_fail'], completion_error=deps['ChatError'], emit_json=deps['emit_json'], redact=deps['redact'], render=deps['render_result'])

_IMPL = {'error_message': error_message, 'classify_error': classify_error, 'auth_probe': auth_probe, 'refuse_if_secrets': refuse_if_secrets, '_retry_delay': _retry_delay, '_cache_key': _cache_key, '_cache_get': _cache_get, '_cache_put': _cache_put, 'log_usage': log_usage, 'usage_exit': usage_exit, 'stream_completion': stream_completion, '_session_or': _session_or, '_completion_dependencies': _completion_dependencies, 'complete': complete, 'chat': chat}

def build(namespace, specification):
    """Build client adapters over a read-only live facade namespace."""
    deps = MappingProxyType(namespace)
    adapters = []
    for item in specification.split():
        public, separator, target = item.partition("=")
        target = target if separator else public
        implementation = _IMPL.get(target)
        if not public.isidentifier() or implementation is None:
            raise ValueError(f"unknown client facade adapter: {item}")
        def adapter(*args, _implementation=implementation, **kwargs):
            return _implementation(*args, deps=deps, **kwargs)
        adapter = functools.update_wrapper(adapter, implementation)
        adapter.__name__ = public
        adapter.__qualname__ = public
        adapters.append(adapter)
    return tuple(adapters)

__all__ = ("build",)
