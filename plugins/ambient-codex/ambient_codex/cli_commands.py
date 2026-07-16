"""Late-bound command-composition adapters for the Ambient CLI facade."""

import functools
from types import MappingProxyType

def _context_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_context_command'].ContextDependencies.bind, 'ABS_MAX_CHARS CHUNK_CHARS CODE_MAP_BUDGET_DEFAULT CODE_MAP_BUDGET_MAX CODE_MAP_SIGS_PER_FILE MAX_PARALLEL_CHUNKS SIG_SCAN_LINE_MAX STDIN_WAIT_MAX_S STDIN_WAIT_S _CHUNK_IDX_TOKEN _C_KEYWORDS _C_TYPE_SIGNATURE_MARKERS _SIG_EXT _SIG_PATTERNS _argv_command _chunk_break_lines _chunking _fail_exit _file_signatures _intake_core _map_reduce_core _py_break_lines _read_stdin_bounded _sigs_python _sigs_regex _stdin_read_and_decode ast fcntl os re response_format_for select sys threading')

def _map_reduce_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_map_reduce_command'].MapReduceDependencies.bind, 'CACHE_TTL_DEFAULT ChatError NetworkError RequestSpec _CHUNK_IDX_TOKEN _cache_get _cache_key _cache_put _chunk_ranges _map_note _map_reduce_core _reduce_response_format _resolve_parallel _retry_delay _session_or complete model_profile concurrent dataclasses os sys threading time')

def run_map_reduce(api_key, api_url, model, map_system, chunks, args, synth_system, reduce_budget, reducer=None, code_map='', gate=None, cancel_event=None, reduce_model=None, catalog=None, session=None, *, deps):
    return deps['_map_reduce_command'].run_map_reduce(api_key, api_url, model, map_system, chunks, args, synth_system, reduce_budget, reducer, code_map, gate, cancel_event, reduce_model, catalog, session, deps['_map_reduce_dependencies']())

def _output_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_output_command'].OutputDependencies.bind, 'CHARS_PER_TOKEN EXIT_PARTIAL _json_in_argv _json_mode _output_schema _reasoning_hint emit_json_error paint redact savings_note savings_note_by_served')

def _reasoning_hint(content, completion_tokens, *, deps):
    return deps['_output_command'].reasoning_hint(content, completion_tokens, deps['_output_dependencies']())

def render_result(text, partial, reason, args, api_key, usage=None, model=None, already_streamed=False, usage_by_model=None, *, deps):
    return deps['_output_command'].render_result(text, partial, reason, args, api_key, usage, model, already_streamed, usage_by_model, deps['_output_dependencies']())

def _public_usage(usage, *, deps):
    return deps['_output_command'].public_usage(usage, deps['_output_dependencies']())

def emit_json(kind, *, model, api_key='', content=None, findings=None, verdict=None, partial=False, reason=None, usage=None, finish_reason=None, extra=None, allow_partial=False, exit_now=True, deps):
    return deps['_output_command'].emit_json(kind, model=model, api_key=api_key, content=content, findings=findings, verdict=verdict, partial=partial, reason=reason, usage=usage, finish_reason=finish_reason, extra=extra, allow_partial=allow_partial, exit_now=exit_now, deps=deps['_output_dependencies']())

def _json_mode(args, *, deps):
    return deps['_output_command'].json_mode(args)

def _json_in_argv(*, deps):
    return deps['_output_command'].json_in_argv()

def emit_json_error(kind, category, diagnosis, api_key='', exit_code=1, *, deps):
    return deps['_output_command'].emit_json_error(kind, category, diagnosis, api_key, exit_code, deps['_output_dependencies']())

def _fail(args, kind, err, api_key='', *, deps):
    return deps['_output_command'].fail(args, kind, err, api_key, deps['_output_dependencies']())

def _argv_command(*, deps):
    return deps['_output_command'].argv_command()

def _fail_exit(args, kind, category, msg, exit_code=1, api_key='', prose=None, *, deps):
    return deps['_output_command'].fail_exit(args, kind, category, msg, exit_code, api_key, prose, deps['_output_dependencies']())

def _humanize_ctx(n, *, deps):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '?'
    return f'{n // 1000}K' if n >= 1000 else str(n)

def _catalog_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_catalog_command'].CatalogDependencies.bind, 'KEY_CONSOLE_URL LAUNCHER_NAME _as_bool _dedupe_catalog _dedupe_catalog_ids _humanize_ctx _split_csv curation fetch_models format_model_line is_auto_model is_hidden paint read_config_file resolve_api_url resolve_key_and_backend resolve_model safe_catalog sanitize save_config_values usage_exit')

def _model_badges(m, *, deps):
    return deps['_catalog_command'].model_badges(m)

def format_model_line(m, chat_default, code_default, note=None, hidden=False, *, deps):
    return deps['_catalog_command'].format_model_line(m, chat_default, code_default, note, hidden, deps['_catalog_dependencies']())

def _dedupe_catalog(models, *, deps):
    return deps['_catalog_command'].dedupe_catalog(models)

def _dedupe_catalog_ids(ids, *, deps):
    return deps['_catalog_command'].dedupe_catalog_ids(ids)

def cmd_models(args, api_key, api_url, conf, *, deps):
    return deps['_catalog_command'].run_models(args, api_key, api_url, conf, deps['_catalog_dependencies']())

def cmd_use(args, api_key, api_url, conf, *, deps):
    return deps['_catalog_command'].run_use(args, api_key, api_url, conf, deps['_catalog_dependencies']())

def cmd_curate(args, *, deps):
    return deps['_catalog_command'].run_curate(args, deps['_catalog_dependencies']())

def _ask_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_ask_command'].AskDependencies.bind, 'CACHE_TTL_DEFAULT ChatError EXIT_PARTIAL MIN_REASONING_CHUNK NetworkError RequestSpec Session _StreamRedactor _answers_agreement _as_pos_int _ask_consensus _best_of_chat _cache_get _cache_key _cache_put _fail _parse_consensus_models _resolve_best_of _resolve_parallel _session_or apply_output_budget complete density_factor emit_json model_profile note_if_hidden pack_chunks paint read_stdin_if_piped redact refuse_if_secrets render_result resolve_reduce_model route_model run_map_reduce savings_note select_best_sample usage_exit warn_if_stdin_ignored')

def cmd_ask(args, api_key, api_url, conf, *, deps):
    return deps['_ask_command'].run_ask(args, api_key, api_url, conf, deps['_ask_dependencies']())

def _resolve_best_of(args, *, deps):
    return deps['_ask_command'].resolve_best_of(args, deps['_ask_dependencies']())

def select_best_sample(texts, *, deps):
    return deps['_ask_command'].select_best_sample(texts)

def _best_of_chat(api_key, api_url, model, messages, args, k, catalog, conf, kind, session=None, *, deps):
    return deps['_ask_command'].run_best_of_chat(api_key, api_url, model, messages, args, k, catalog, conf, kind, session, deps['_ask_dependencies']())

def _answers_agreement(texts, *, deps):
    return deps['_ask_command'].answers_agreement(texts)

def _ask_consensus(args, api_key, api_url, conf, catalog, question, doc, session=None, *, deps):
    return deps['_ask_command'].run_ask_consensus(args, api_key, api_url, conf, catalog, question, doc, session, deps['_ask_dependencies']())

def _read_map_item(path, *, deps):
    """(text, error) for ONE file item. Mirrors read_files' safety checks
    (regular file, binary, size) but reports PER-ITEM instead of skipping or
    aborting — map's unit of failure is the item, never the batch."""
    return deps['_intake_core'].read_map_item(path, deps['ABS_MAX_CHARS'])

def _map_gather_items(args, *, deps):
    """The per-item work list: [(id, text, pre_error)] by the documented
    precedence — positional paths first (each FILE is one item), else stdin
    (one item per non-empty line; --jsonl parses each line as a JSON object
    with an "input" field and optional "id")."""
    items = []
    if args.paths:
        deps['warn_if_stdin_ignored']('map reads stdin only when no paths are given')
        total, capped = (0, False)
        cap_msg = f"batch input cap reached ({deps['ABS_MAX_CHARS']:,} chars total) — item skipped to bound memory; split the map into smaller runs"
        for path in args.paths:
            if capped:
                items.append((path, None, cap_msg))
                continue
            text, err = deps['_read_map_item'](path)
            if err is None:
                total += len(text)
                if total > deps['ABS_MAX_CHARS']:
                    capped = True
                    items.append((path, None, cap_msg))
                    continue
            items.append((path, text, err))
        return items
    data = deps['read_stdin_if_piped']()
    idx = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        idx += 1
        if not getattr(args, 'jsonl', False):
            items.append((idx, line, None))
            continue
        try:
            obj = deps['json'].loads(line)
        except deps['json'].JSONDecodeError as err:
            items.append((idx, None, f'invalid JSONL line ({err})'))
            continue
        if not isinstance(obj, dict):
            items.append((idx, None, 'JSONL line is not a JSON object'))
            continue
        item_id = obj.get('id', idx)
        text = obj.get('input')
        if not isinstance(text, str) or not text.strip():
            items.append((item_id, None, 'JSONL item needs a non-empty string "input" field'))
            continue
        items.append((item_id, text, None))
    return items

def _emit_map_result(args, api_key, item_id, *, content=None, partial=False, cached=False, category=None, diagnosis=None, deps):
    """Stream ONE per-item result the moment it completes. Items finish out
    of order, so the id always rides along. Under --json each result is ONE
    LINE — a self-contained map envelope (JSONL), redacted like every other
    surface. Returns the item's exit code (0 ok / 1 error / 2 partial)."""
    if content is None:
        status, code = ('error', 1)
    elif partial:
        status, code = ('partial', deps['EXIT_PARTIAL'])
    else:
        status, code = ('ok', 0)
    if deps['_json_mode'](args):
        env = {'schema_version': 1, 'kind': 'map', 'status': status, 'id': item_id, 'content': content, 'exit_code': code}
        if category is not None:
            env['category'] = category
        if diagnosis is not None:
            env['diagnosis'] = diagnosis
        if cached:
            env['cached'] = True
        print(deps['redact'](deps['json'].dumps(env), api_key), flush=True)
        return code
    if status == 'error':
        print(deps['redact'](f'===== ITEM {item_id} — FAILED [{category}]: {diagnosis}', api_key), flush=True)
        return code
    note = ' — PARTIAL (truncated)' if partial else ' (cached)' if cached else ''
    print(deps['redact'](f'===== ITEM {item_id}{note} =====\n{content}\n', api_key), flush=True)
    return code

def _map_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_map_command'].MapDependencies.bind, 'CACHE_TTL_DEFAULT ChatError EXIT_PARTIAL EXIT_USAGE MAP_OVERSIZE_MSG NetworkError Session _cache_get _cache_key _cache_put _emit_map_result _fail_exit _map_gather_items _map_workflow _resolve_parallel _retry_delay apply_output_budget complete density_factor model_profile note_if_hidden refuse_if_secrets route_model threading')

def cmd_map(args, api_key, api_url, conf, *, deps):
    return deps['_map_command'].run_map(args, api_key, api_url, conf, deps['_map_dependencies']())

def _audit_input_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_audit_inputs'].AuditInputDependencies.bind, 'ABS_MAX_CHARS AUDIT_FINDINGS_SCHEMA AUDIT_JSON_INSTRUCTION AUDIT_SYSTEM_PROMPT ChatError EXIT_USAGE NetworkError REPO_FILE_MAX_BYTES REPO_LOCKFILES REPO_SKIP_DIRS RequestSpec _NON_FILE_LABELS _as_pos_int _audit_core _audit_prose _fail_exit _guttered_size _intake_core _repo_candidate_paths _repository_core _session_or _text_has_unparsed_finding _verdict_from adaptive_response_format complete cross_file_suspects dataclasses dedupe_findings emit_json extract_json files_block json os paint parse_audit_object parse_prose_findings read_files record_cap redact repo_walk sanitize subprocess sys with_line_gutters')

def _audit_hook_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_audit_hook_command'].AuditHookDependencies.bind, 'AMBIENT_HOOK_MARKER LAUNCHER_NAME LEGACY_AMBIENT_HOOK_MARKERS EXIT_USAGE _bundled_cli_path _fail_exit _git_hooks_dir _hook_is_ours _render_hook contextlib os shlex shutil sys tempfile subprocess usage_exit')

def _render_hook(name, *, deps):
    return deps['_audit_hook_command']._render_hook(name, deps=deps['_audit_hook_dependencies']())

def _git_hooks_dir(args, *, deps):
    return deps['_audit_hook_command']._git_hooks_dir(args, deps=deps['_audit_hook_dependencies']())

def _hook_is_ours(existing, name, *, deps):
    return deps['_audit_hook_command']._hook_is_ours(existing, name, deps=deps['_audit_hook_dependencies']())

def cmd_audit_hook(args, *, deps):
    return deps['_audit_hook_command'].cmd_audit_hook(args, deps=deps['_audit_hook_dependencies']())

def _audit_planning_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_audit_planning'].AuditPlanningDependencies.bind, 'AUDIT_FINDINGS_SCHEMA AUDIT_JSON_INSTRUCTION AUDIT_SYNTH_PROMPT CACHE_TTL_DEFAULT ChatError EXIT_USAGE MIN_REASONING_CHUNK RequestSpec _CHUNK_IDX_TOKEN _audit_core _audit_sample_prep _audit_single_key _cache_get _cache_key _cache_put _fail_exit _map_note _session_or _audit_split_estimate _consensus_estimate adaptive_response_format build_code_map code_map_budget complete contextlib dataclasses difflib estimate_cost estimate_cost_fb estimate_cost_mr estimate_cost_mr_fb extract_json files_block findings_reducer json model_profile pack_chunks parse_audit_object redact run_map_reduce sys usage_exit')

def _audit_sample_prep(model, catalog, labeled, sys_prompt, args, *, deps):
    return deps['_audit_planning']._audit_sample_prep(model, catalog, labeled, sys_prompt, args, deps=deps['_audit_planning_dependencies']())

def _audit_single_key(model, sp, labeled, a, *, deps):
    return deps['_audit_planning']._audit_single_key(model, sp, labeled, a, deps=deps['_audit_planning_dependencies']())

def run_one_audit(model, catalog, labeled, sys_prompt, args, api_key, api_url, conf, gate=None, cancel_event=None, session=None, *, deps):
    return deps['_audit_planning'].run_one_audit(model, catalog, labeled, sys_prompt, args, api_key, api_url, conf, gate, cancel_event, session, deps=deps['_audit_planning_dependencies']())

def _audit_split_estimate(catalog, model, reduce_model, labeled, total, eff_total, profile, dens, max_tokens, structured, fb_args=None, fb_conf=None, *, deps):
    return deps['_audit_planning']._audit_split_estimate(catalog, model, reduce_model, labeled, total, eff_total, profile, dens, max_tokens, structured, fb_args, fb_conf, deps=deps['_audit_planning_dependencies']())

def _parse_consensus_models(args, catalog, api_key, *, deps):
    return deps['_audit_planning']._parse_consensus_models(args, catalog, api_key, deps=deps['_audit_planning_dependencies']())

def _consensus_estimate(catalog, models, labeled, total, explicit_max_tokens=None, fb_args=None, fb_conf=None, *, deps):
    return deps['_audit_planning']._consensus_estimate(catalog, models, labeled, total, explicit_max_tokens, fb_args, fb_conf, deps=deps['_audit_planning_dependencies']())

def _best_of_audit_misses(catalog, model, labeled, sys_prompt, args, k, explicit_max_tokens, original_max_tokens, *, deps):
    return deps['_audit_planning']._best_of_audit_misses(catalog, model, labeled, sys_prompt, args, k, explicit_max_tokens, original_max_tokens, deps=deps['_audit_planning_dependencies']())

def _print_repo_plan(meta, catalog, model, reduce_model, labeled, total, eff_total, profile, dens, args, api_key, consensus_models=None, best_of=None, explicit_mt=None, conf=None, *, deps):
    return deps['_audit_planning']._print_repo_plan(meta, catalog, model, reduce_model, labeled, total, eff_total, profile, dens, args, api_key, consensus_models, best_of, explicit_mt, conf, deps=deps['_audit_planning_dependencies']())

def _audit_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_audit_command'].AuditDependencies.bind, 'AUDIT_FINDINGS_SCHEMA AUDIT_JSON_INSTRUCTION AUDIT_SYNTH_PROMPT AUDIT_SYSTEM_PROMPT ChatError EXIT_PARTIAL EXIT_USAGE MIN_REASONING_CHUNK NetworkError RequestSpec SEVERITY_ORDER Session _audit_split_estimate _best_of_audit_misses _consensus_estimate _fail _fail_exit _finding_sig _parse_consensus_models _print_repo_plan _resolve_best_of _resolve_parallel _titles_match _verdict_from adaptive_response_format apply_output_budget build_code_map code_map_budget complete density_factor emit_json files_block findings_reducer git_diff_inputs model_profile note_if_hidden pack_chunks parse_audit_object read_files read_stdin_if_piped redact refuse_if_secrets render_findings render_result repo_audit_inputs resolve_model resolve_reduce_model route_model run_cross_file_pass run_map_reduce run_one_audit usage_exit warn_if_stdin_ignored with_line_gutters')

def cmd_audit(args, api_key, api_url, conf, *, deps):
    return deps['_audit_command'].run_audit(args, api_key, api_url, conf, deps['_audit_dependencies']())

def _generation_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_generation_commands'].GenerationDependencies.bind, 'MIN_REASONING_CHUNK Session _best_of_chat _code_workflow _fail_exit _resolve_best_of apply_output_budget chat density_factor files_block model_profile note_if_hidden pack_chunks read_files refuse_if_secrets resolve_reduce_model route_model run_map_reduce usage_exit warn_if_stdin_ignored CHAT_HELP ChatError EXIT_USAGE NetworkError RequestSpec _StreamRedactor _chat_input _line_has_secret _stdin_is_tty _trim_chat_history complete is_auto_model redact resolve_auto_model savings_note')

def cmd_code(args, api_key, api_url, conf, *, deps):
    return deps['_generation_commands'].run_code(args, api_key, api_url, conf, deps['_generation_dependencies']())

def _stdin_is_tty(*, deps):
    try:
        return deps['sys'].stdin.isatty()
    except Exception:
        return False

def _chat_input(prompt_str, *, deps):
    """One REPL line (isolated for testability; readline, when importable,
    gives it line editing + in-session history for free)."""
    return input(prompt_str)

def _trim_chat_history(history, budget_chars, *, deps):
    """Return a NEW history list that fits `budget_chars`, dropping the
    OLDEST turns first (user+assistant pairs, then singles). The most recent
    exchange always survives — a REPL must never truncate what the user just
    said in favor of stale turns."""
    return deps['_chat_workflow'].trim_history(history, budget_chars)

def cmd_chat(args, api_key, api_url, conf, *, deps):
    return deps['_generation_commands'].run_chat(args, api_key, api_url, conf, deps['_generation_dependencies']())

def _build_state_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_build_state_command'].BuildStateDependencies.bind, 'SECRET_NAMES_RE __version__ _build_state_path _build_workflow json os safe_relpath sys tempfile')

def _parse_file_records(text, *, deps):
    return deps['_build_state_command']._parse_file_records(text, deps=deps['_build_state_dependencies']())

def _within_root(child_real, root_real, *, deps):
    return deps['_build_state_command']._within_root(child_real, root_real, deps=deps['_build_state_dependencies']())

def safe_relpath(path, root, *, deps):
    return deps['_build_state_command'].safe_relpath(path, root, deps=deps['_build_state_dependencies']())

def _build_state_path(root, *, deps):
    return deps['_build_state_command']._build_state_path(root, deps=deps['_build_state_dependencies']())

def build_resume_identity(*, task, model, reduce_model, context_paths, raw_context_sha, max_files, max_file_bytes, max_tokens, temperature, deps):
    return deps['_build_state_command'].build_resume_identity(task=task, model=model, reduce_model=reduce_model, context_paths=context_paths, raw_context_sha=raw_context_sha, max_files=max_files, max_file_bytes=max_file_bytes, max_tokens=max_tokens, temperature=temperature, deps=deps['_build_state_dependencies']())

def _save_build_state(root, state, *, deps):
    return deps['_build_state_command']._save_build_state(root, state, deps=deps['_build_state_dependencies']())

def _load_build_state(root, task_sha, max_plan=512, max_file_bytes=None, *, deps):
    return deps['_build_state_command']._load_build_state(root, task_sha, max_plan, max_file_bytes, deps=deps['_build_state_dependencies']())

def _build_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_build_command'].BuildDependencies.bind, 'BUILD_FAILURE_CIRCUIT_BREAKER BUILD_FILES_SCHEMA BUILD_GEN_PROMPT BUILD_JSON_INSTRUCTION_PLAN BUILD_PLAN_PROMPT ChatError DEFAULT_CODE_MODEL DIRECT_BUILD_BATCH_FILES EXIT_PARTIAL MAX_BUILD_FILES MAX_BUILD_FILE_BYTES MIN_REASONING_CHUNK REASONING_BUILD_BATCH_FILES Session _build_apply _build_workflow _context_safe_escalation_ceiling _effective_cpt _fail _fail_exit _json_mode _load_build_state _parse_file_records _resolve_parallel _save_build_state _served_model_of _stdin_is_tty _within_root apply_output_budget build_plan_rf_ladder build_resume_identity cap_state complete density_factor emit_json emit_json_error estimate_cost_fb estimate_cost_mr_fb extract_json files_block is_auto_model model_profile note_if_hidden pack_chunks progress_display_enabled read_files record_cap redact refuse_if_secrets resolve_model resolve_reduce_model response_format_for route_model run_map_reduce safe_relpath usage_exit warn_if_stdin_ignored')

def cmd_build(args, api_key, api_url, conf, *, deps):
    return deps['_build_command'].run_build(args, api_key, api_url, conf, deps['_build_dependencies']())

def cmd_mode(args, *, deps):
    """Delegate-mode flag — an ordered level: off < on < takeover (takeover
    implies delegate). Works without credentials (config-file only)."""
    return deps['_settings_commands'].run_mode(args, deps['_settings_commands'].ModeDependencies(save_config=deps['save_config_values'], read_config=deps['read_config_file'], resolve_key=deps['resolve_key_and_backend'], resolve_model=deps['resolve_model'], launcher_name=deps['LAUNCHER_NAME']))

def _config_fmt_num(x, *, deps):
    """5.0 → '5', 1.5 → '1.5' — clean display of an often-integral float."""
    return deps['_settings_commands'].format_number(x)

def _config_norm_bool(raw, *, deps):
    """on/off/true/false/1/0/yes/no (any case) → 'on'/'off', else ValueError.
    Stored as the literal on/off, which every boolean reader reads identically:
    PROGRESS/FLEET treat off/0/false/no as off (else on); FALLBACK treats
    1/on/true as on, and 'off' is outside that set → off."""
    return deps['_settings_commands'].normalize_bool(raw)

def _config_norm_price(raw, *, deps):
    """A frontier reference price ('in/out' pair like 3/15, or one blended figure)."""
    return deps['_settings_commands'].normalize_price(raw, deps['parse_reference_price'])

def _config_names(*, deps):
    return deps['_settings_commands'].config_names(deps['CONFIG_SETTINGS'])

def _config_env_shadow_note(env, *, deps):
    """Warn (once, on stderr) when an exported env var will shadow the file write —
    so a user who sets a value but sees no change understands the precedence."""
    return deps['_settings_commands'].env_shadow_note(env, deps['os'].environ)

def _config_redact(text, *, deps):
    """Echo an unknown setting name back ONLY if it is a short, clean slug (a
    plausible typo like 'streming'). Anything else — a mistyped key, a value with
    spaces/unicode, a vendor-format secret, a long opaque token — is replaced with a
    placeholder, so nothing secret-shaped can ever reach stderr (Codex 2026-07-08:
    a whitelist of safe SHAPE beats a blocklist of secret shapes)."""
    return deps['_settings_commands'].redact_unknown(text, deps['SECRET_PATTERNS'])

def _config_curation_summary(conf, *, deps):
    """Zero-network: whether any curation is configured (no catalog fetch)."""
    return deps['_settings_commands'].curation_summary(conf, deps['os'].environ)

def _config_print_status(*, deps):
    """The `ambient-codex config` table. Two aligned blocks: settings OWNED by other
    commands (pointers, not duplicated), then the config-owned knobs. Purely local
    reads — the API key VALUE is never printed, only its backend."""
    return deps['_settings_commands'].print_config_status(deps['_settings_dependencies']())

def _settings_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_settings_commands'].ConfigDependencies, 'settings=CONFIG_SETTINGS save_config=save_config_values read_config=read_config_file resolve_key=resolve_key_and_backend resolve_model usage_error=usage_exit launcher_name=LAUNCHER_NAME secret_patterns=SECRET_PATTERNS environ=os.environ')

def cmd_config(args, *, deps):
    """User settings in one place — no env vars, no hand-editing the config file.
    Keyless (config-only, no network). `ambient-codex config` shows everything; `set`/
    `unset` change one whitelisted knob."""
    return deps['_settings_commands'].run_config(args, deps['_settings_dependencies']())

def _control_catalog(conf, include_all=False, offline=False, *, deps):
    """Best-effort catalog for the native control panel.

    Status must never fail just because the catalog endpoint is unreachable or a
    local endpoint setting is unsafe. If endpoint validation refuses the URL, we
    skip model fetching instead of sending a key anywhere unexpected.
    """
    return deps['_settings_commands'].control_catalog(conf, deps['_control_dependencies'](), include_all, offline)

def _control_settings(conf, *, deps):
    return deps['_settings_commands'].control_settings(conf, deps['_control_dependencies']())

def _control_setting_names(*, deps):
    return deps['_settings_commands'].control_setting_names(deps['CONTROL_SETTING_NAMES'])

def _control_model_item(model, chat_default, code_default, hidden_ids, notes, *, deps):
    return deps['_settings_commands'].control_model_item(model, chat_default, code_default, hidden_ids, notes, deps['_as_bool'])

def _control_snapshot(include_all=False, offline=False, *, deps):
    return deps['_settings_commands'].control_snapshot(deps['_control_dependencies'](), include_all, offline)

def _control_print_status(payload, *, deps):
    return deps['_settings_commands'].print_control_status(payload, deps['_control_dependencies']())

def _control_print_key_status(*, deps):
    return deps['_settings_commands'].print_control_key_status(deps['_control_dependencies']())

def _control_setup_instruction(action, *, deps):
    return deps['_settings_commands'].control_setup_instruction(action, deps['_control_dependencies']())

def _control_key(args, *, deps):
    return deps['_settings_commands'].control_key(args, deps['_control_dependencies']())

def _control_model(args, *, deps):
    return deps['_settings_commands'].control_model(args, deps['_control_dependencies']())

def _control_setting(args, *, deps):
    return deps['_settings_commands'].control_setting(args, deps['_control_dependencies']())

def _control_menu(args, *, deps):
    return deps['_settings_commands'].control_menu(args, deps['_control_dependencies']())

def _control_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_settings_commands'].ControlDependencies, 'version=__version__ key_console_url=KEY_CONSOLE_URL launcher_name=LAUNCHER_NAME mode_options=CONTROL_MODE_OPTIONS workflows=CONTROL_WORKFLOWS chat_actions=CONTROL_CHAT_ACTIONS actions=CONTROL_ACTIONS setting_names=CONTROL_SETTING_NAMES settings=CONFIG_SETTINGS environ=os.environ read_config=read_config_file resolve_key=resolve_key_and_backend resolve_model resolve_api_url safe_catalog dedupe_catalog=_dedupe_catalog as_bool=_as_bool curation is_hidden config_curation_summary=_config_curation_summary config_redact=_config_redact usage_error=usage_exit command_setup=cmd_setup command_use=cmd_use command_config=cmd_config command_mode=cmd_mode command_doctor=cmd_doctor command_usage=cmd_usage')

def cmd_control(args, *, deps):
    """Codex-native control panel over mode, models, key lifecycle, settings.

    This command is intentionally keyless at dispatch: it can show status, remove
    a key, change local mode/settings, and browse the public catalog before first
    setup. It never accepts API key material as an argument.
    """
    return deps['_settings_commands'].run_control(args, deps['_control_dependencies']())

def _collect_key_interactive(*, deps):
    """Prompt for the key with hidden input, local pre-validation, and up to
    3 attempts (a stray Enter or a mangled paste re-prompts instead of
    aborting the whole flow)."""
    return deps['_setup_command'].collect_key_interactive(deps['_setup_dependencies']())

def _verify_and_store_key(key, conf, use_file, *, deps):
    """Verify a key with a real authenticated completion, then store it —
    Keychain/libsecret preferred, 0600 env file otherwise. Returns
    (models, probe_detail, where, funds_issue). Exits with a per-category
    diagnosis + support pointer when the key can't be certified."""
    return deps['_setup_command'].verify_and_store_key(key, conf, use_file, deps['_setup_dependencies']())

def _setup_remove(*, deps):
    """Offboarding: delete the stored key everywhere it may live. Removing the
    key naturally re-arms first-use onboarding (state IS key presence)."""
    return deps['_setup_command'].setup_remove(deps['_setup_dependencies']())

def _setup_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_setup_command'].SetupDependencies, 'preamble=SETUP_PREAMBLE launcher_name=LAUNCHER_NAME key_console_url=KEY_CONSOLE_URL support_line=SUPPORT_LINE default_api_url=DEFAULT_API_URL config_path=CONFIG_PATH api_key_env=API_KEY_ENV keychain_service=KEYCHAIN_SERVICE read_config=read_config_file save_config=save_config_values resolve_key=resolve_key_and_backend resolve_api_url key_paste_problem api_request classify_error catalog_data=_catalog_data auth_probe network_error=NetworkError keychain_available keychain_write keychain_delete keychain_read secret_backend print_welcome=print_welcome_panel')

def cmd_setup(args, *, deps):
    """First-run key onboarding: collect, locally pre-validate, verify with a
    real authenticated completion, store securely, then show the welcome
    panel. --remove offboards; --force rotates."""
    return deps['_setup_command'].run_setup(args, deps['_setup_dependencies']())

def _doctor_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_doctor_command'].DoctorDependencies, 'version=__version__ config_path=CONFIG_PATH launcher_name=LAUNCHER_NAME shared_api_key_env=SHARED_API_KEY_ENV api_key_env=API_KEY_ENV opencode_config_path=OPENCODE_CONFIG_PATH opencode_provider=OPENCODE_PROVIDER cache_dir=CACHE_DIR read_config=read_config_file resolve_key=resolve_key_and_backend resolve_api_url sanitize paint which=shutil.which keychain_available shared_key_env_is_set api_request network_error=NetworkError classify_error catalog_data=_catalog_data dedupe_catalog=_dedupe_catalog ready_model_ids auth_probe curation resolve_model is_auto_model is_hidden bundled_cli_path=_bundled_cli_path stable_launcher_is_ours=_stable_launcher_is_ours')

def cmd_doctor(args, *, deps):
    return deps['_doctor_command'].run_doctor(args, deps['_doctor_dependencies']())

def _print_empty_usage(args, note, message, *, deps):
    """Render no local usage as a successful, schema-stable summary."""
    return deps['_maintenance_commands'].print_empty_usage(args, note, message)

def _usage_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_maintenance_commands'].UsageDependencies, 'usage_path=USAGE_PATH read_config=read_config_file savings_enabled=_savings_enabled report=_usage_report positive_int=_as_pos_int resolve_api_url fetch_models model_pricing resolve_reference_price network_error=NetworkError usage_error=usage_exit now=time.time')

def cmd_usage(args, *, deps):
    """Local spend summary (Ambient exposes no balance endpoint)."""
    return deps['_maintenance_commands'].run_usage(args, deps['_usage_dependencies']())

def _this_script(*, deps):
    """Absolute real path of the running CLI (symlink-resolved)."""
    return deps['os'].path.realpath(deps['os'].path.abspath(deps['sys'].argv[0] or deps['__file__']))

def _bundled_cli_path(*, deps):
    """Absolute real path of THIS source file.

    `_this_script()` reads sys.argv[0], which is what `link` wants but is wrong
    when the module is imported rather than executed (argv[0] is then `-c`, a
    test runner, or a REPL). Anything baked into a generated artifact must use
    the module's own path.
    """
    return deps['os'].path.realpath(deps['os'].path.abspath(deps['__file__']))

def _link_is_ours(dest, *, deps):
    """True only if `dest` is a launcher symlink WE own — its stored target has
    an `/ambient-codex/` path component (every real install: dev
    `.../ambient-codex/...` or marketplace `.../ambient-codex/<ver>/...`).
    A symlink to a DIFFERENT tool merely named `ambient` returns False, so we
    never clobber a foreign launcher. readlink reports the stored target of a
    dangling link too, so the same check covers the post-GC case."""
    return deps['_launcher'].owned_link(dest, is_link=deps['os'].path.islink, read_link=deps['os'].readlink)

def _shim_is_ours(shim_path, *, deps):
    """Windows analogue of _link_is_ours for the `.cmd` shim (M14): true only if
    the file is OUR template AND the target has an `/ambient-codex/` component — so
    `--remove` never deletes a foreign shim. Matches BOTH the current
    `@"<interp>" "<target>" %*` form and the legacy `@python "<target>" %*` one
    so an older shim stays removable."""

    def read_text(path):
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    return deps['_launcher'].owned_shim(shim_path, read_text=read_text)

def _stable_launcher_asset(*, deps):
    """Return the self-contained launcher shipped beside this CLI."""
    root = deps['os'].path.dirname(deps['os'].path.dirname(deps['_bundled_cli_path']()))
    asset = deps['os'].path.join(root, 'scripts', 'ambient-codex-launcher.py')
    if not deps['os'].path.isfile(asset):
        raise OSError('the bundled stable launcher is missing')
    return asset

def _stable_launcher_is_ours(path, *, deps):

    def read_text(candidate):
        with open(candidate, encoding='utf-8') as fh:
            return fh.read()
    return deps['_launcher'].owned_file(path, read_text=read_text)

def _write_stable_launcher(source, destination, *, deps):
    """Atomically install the standalone launcher without following links."""
    return deps['_launcher_command'].write_stable_launcher(source, destination)

def _launcher_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_launcher_command'].LauncherDependencies, 'launcher_name=LAUNCHER_NAME stable_launcher_marker=_launcher.STABLE_LAUNCHER_MARKER link_is_ours=_link_is_ours shim_is_ours=_shim_is_ours stable_launcher_asset=_stable_launcher_asset stable_launcher_is_ours=_stable_launcher_is_ours write_stable_launcher=_write_stable_launcher')

def cmd_link(args, *, deps):
    """Put a cache-rotation-safe `ambient-codex` launcher on PATH.

    The copied wrapper resolves the active Codex MCP install when it is invoked,
    rather than pointing at a versioned plugin-cache directory. The launcher is
    deliberately not named `ambient`: another Ambient install may own that name.
    """
    return deps['_launcher_command'].run_link(args, deps['_launcher_dependencies']())

def cmd_cache(args, *, deps):
    """Local data lifecycle: inspect or purge the chunk cache (entries hold
    model output that quotes your code; TTL 7 days, 0600)."""
    return deps['_maintenance_commands'].run_cache(args, deps['_maintenance_commands'].CacheDependencies(cache_dir=deps['CACHE_DIR'], usage_error=deps['usage_exit'], now=deps['time'].time))

def ensure_opencode_config(api_url, model, *, deps):
    """Fresh-install fix: create/repair the opencode provider config so
    `ambient-codex agent` works out of the box instead of failing on a missing file.
    Only this install's namespaced provider entry is repaired; every other provider
    is preserved byte-for-byte at the decoded-data level. The entry is keyed by
    LAUNCHER_NAME so a second Ambient install cannot share it."""
    return deps['_agent_command'].ensure_provider_config(deps['OPENCODE_CONFIG_PATH'], deps['OPENCODE_PROVIDER'], api_url, model, deps['_agent_config'].update_provider_config)

def _agent_dependencies(*, deps):
    return deps['_facade_adapters'].bind(deps, deps['_agent_command'].AgentDependencies, 'provider=OPENCODE_PROVIDER ensure_config=ensure_opencode_config resolve_model note_if_hidden is_auto_model resolve_auto_model safe_catalog build_argv=_agent_config.build_agent_argv which=shutil.which run_process=subprocess.run')

def cmd_agent(args, api_key, api_url, conf, *, deps):
    return deps['_agent_command'].run_agent(args, api_key, api_url, conf, deps['_agent_dependencies']())

def cmd_codex(args, api_key, api_url, conf, *, deps):
    return deps['_agent_command'].run_codex()

_IMPL = {'_context_dependencies': _context_dependencies, '_map_reduce_dependencies': _map_reduce_dependencies, 'run_map_reduce': run_map_reduce, '_output_dependencies': _output_dependencies, '_reasoning_hint': _reasoning_hint, 'render_result': render_result, '_public_usage': _public_usage, 'emit_json': emit_json, '_json_mode': _json_mode, '_json_in_argv': _json_in_argv, 'emit_json_error': emit_json_error, '_fail': _fail, '_argv_command': _argv_command, '_fail_exit': _fail_exit, '_humanize_ctx': _humanize_ctx, '_catalog_dependencies': _catalog_dependencies, '_model_badges': _model_badges, 'format_model_line': format_model_line, '_dedupe_catalog': _dedupe_catalog, '_dedupe_catalog_ids': _dedupe_catalog_ids, 'cmd_models': cmd_models, 'cmd_use': cmd_use, 'cmd_curate': cmd_curate, '_ask_dependencies': _ask_dependencies, 'cmd_ask': cmd_ask, '_resolve_best_of': _resolve_best_of, 'select_best_sample': select_best_sample, '_best_of_chat': _best_of_chat, '_answers_agreement': _answers_agreement, '_ask_consensus': _ask_consensus, '_read_map_item': _read_map_item, '_map_gather_items': _map_gather_items, '_emit_map_result': _emit_map_result, '_map_dependencies': _map_dependencies, 'cmd_map': cmd_map, '_audit_input_dependencies': _audit_input_dependencies, '_audit_hook_dependencies': _audit_hook_dependencies, '_render_hook': _render_hook, '_git_hooks_dir': _git_hooks_dir, '_hook_is_ours': _hook_is_ours, 'cmd_audit_hook': cmd_audit_hook, '_audit_planning_dependencies': _audit_planning_dependencies, '_audit_sample_prep': _audit_sample_prep, '_audit_single_key': _audit_single_key, 'run_one_audit': run_one_audit, '_audit_split_estimate': _audit_split_estimate, '_parse_consensus_models': _parse_consensus_models, '_consensus_estimate': _consensus_estimate, '_best_of_audit_misses': _best_of_audit_misses, '_print_repo_plan': _print_repo_plan, '_audit_dependencies': _audit_dependencies, 'cmd_audit': cmd_audit, '_generation_dependencies': _generation_dependencies, 'cmd_code': cmd_code, '_stdin_is_tty': _stdin_is_tty, '_chat_input': _chat_input, '_trim_chat_history': _trim_chat_history, 'cmd_chat': cmd_chat, '_build_state_dependencies': _build_state_dependencies, '_parse_file_records': _parse_file_records, '_within_root': _within_root, 'safe_relpath': safe_relpath, '_build_state_path': _build_state_path, 'build_resume_identity': build_resume_identity, '_save_build_state': _save_build_state, '_load_build_state': _load_build_state, '_build_dependencies': _build_dependencies, 'cmd_build': cmd_build, 'cmd_mode': cmd_mode, '_config_fmt_num': _config_fmt_num, '_config_norm_bool': _config_norm_bool, '_config_norm_price': _config_norm_price, '_config_names': _config_names, '_config_env_shadow_note': _config_env_shadow_note, '_config_redact': _config_redact, '_config_curation_summary': _config_curation_summary, '_config_print_status': _config_print_status, '_settings_dependencies': _settings_dependencies, 'cmd_config': cmd_config, '_control_catalog': _control_catalog, '_control_settings': _control_settings, '_control_setting_names': _control_setting_names, '_control_model_item': _control_model_item, '_control_snapshot': _control_snapshot, '_control_print_status': _control_print_status, '_control_print_key_status': _control_print_key_status, '_control_setup_instruction': _control_setup_instruction, '_control_key': _control_key, '_control_model': _control_model, '_control_setting': _control_setting, '_control_menu': _control_menu, '_control_dependencies': _control_dependencies, 'cmd_control': cmd_control, '_collect_key_interactive': _collect_key_interactive, '_verify_and_store_key': _verify_and_store_key, '_setup_remove': _setup_remove, '_setup_dependencies': _setup_dependencies, 'cmd_setup': cmd_setup, '_doctor_dependencies': _doctor_dependencies, 'cmd_doctor': cmd_doctor, '_print_empty_usage': _print_empty_usage, '_usage_dependencies': _usage_dependencies, 'cmd_usage': cmd_usage, '_this_script': _this_script, '_bundled_cli_path': _bundled_cli_path, '_link_is_ours': _link_is_ours, '_shim_is_ours': _shim_is_ours, '_stable_launcher_asset': _stable_launcher_asset, '_stable_launcher_is_ours': _stable_launcher_is_ours, '_write_stable_launcher': _write_stable_launcher, '_launcher_dependencies': _launcher_dependencies, 'cmd_link': cmd_link, 'cmd_cache': cmd_cache, 'ensure_opencode_config': ensure_opencode_config, '_agent_dependencies': _agent_dependencies, 'cmd_agent': cmd_agent, 'cmd_codex': cmd_codex}

def build(namespace, specification):
    """Build command adapters over a read-only live facade namespace."""
    deps = MappingProxyType(namespace)
    adapters = []
    for item in specification.split():
        public, separator, target = item.partition("=")
        target = target if separator else public
        implementation = _IMPL.get(target)
        if not public.isidentifier() or implementation is None:
            raise ValueError(f"unknown CLI command adapter: {item}")
        def adapter(*args, _implementation=implementation, **kwargs):
            return _implementation(*args, deps=deps, **kwargs)
        adapter = functools.update_wrapper(adapter, implementation)
        adapter.__name__ = public
        adapter.__qualname__ = public
        adapters.append(adapter)
    return tuple(adapters)

__all__ = ("build",)
