"""Audit sampling, cache planning, consensus validation, and dry-run plans."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AuditPlanningDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _audit_sample_prep(model, catalog, labeled, sys_prompt, args, deps=None):
    """Shared prep for ONE audit run: (a, sp, single, chunk, total) — the
    replaced RequestSpec (budget + response_format resolved for THIS model),
    the final system prompt, and the sizing numbers. Used by run_one_audit
    AND the best-of miss-plan so precomputed cache keys match the live
    call's BY CONSTRUCTION."""
    AUDIT_FINDINGS_SCHEMA = deps.AUDIT_FINDINGS_SCHEMA
    AUDIT_JSON_INSTRUCTION = deps.AUDIT_JSON_INSTRUCTION
    RequestSpec = deps.RequestSpec
    _audit_core = deps._audit_core
    adaptive_response_format = deps.adaptive_response_format
    model_profile = deps.model_profile
    return _audit_core.prepare_sample(
        model, catalog, labeled, sys_prompt, args,
        model_profile=model_profile,
        request_spec=RequestSpec,
        response_format=adaptive_response_format,
        findings_schema=AUDIT_FINDINGS_SCHEMA,
        json_instruction=AUDIT_JSON_INSTRUCTION,
    )


def _audit_single_key(model, sp, labeled, a, deps=None):
    """Salted content-address for ONE single-shot audit call — shared by
    run_one_audit's cache lane and the best-of miss-plan."""
    _audit_core = deps._audit_core
    _cache_key = deps._cache_key
    files_block = deps.files_block
    return _audit_core.single_shot_key(
        model, sp, labeled, a, files_block=files_block, cache_key=_cache_key)


def run_one_audit(model, catalog, labeled, sys_prompt, args, api_key, api_url,
                  conf, gate=None, cancel_event=None, session=None, deps=None):
    """Structured audit on ONE model → (findings, ok). ok
    is False when the model errored, was unready, or returned unparseable/partial
    output — so consensus can't present a FAILURE as 'no defects'.
    Each model gets its OWN profile/budget. `gate`/`cancel_event` flow
    through to run_map_reduce so the consensus lane's shared cap and fail-fast
    reach every model's inner fan-out. The single-shot path reads/writes the
    same salted cache as the chunked fan-out a re-run — best-of salted
    or not — re-bills only what the cache does not already hold."""
    AUDIT_SYNTH_PROMPT = deps.AUDIT_SYNTH_PROMPT
    CACHE_TTL_DEFAULT = deps.CACHE_TTL_DEFAULT
    ChatError = deps.ChatError
    _audit_sample_prep = deps._audit_sample_prep
    _audit_single_key = deps._audit_single_key
    _cache_get = deps._cache_get
    _cache_put = deps._cache_put
    _session_or = deps._session_or
    build_code_map = deps.build_code_map
    code_map_budget = deps.code_map_budget
    complete = deps.complete
    contextlib = deps.contextlib
    extract_json = deps.extract_json
    files_block = deps.files_block
    findings_reducer = deps.findings_reducer
    pack_chunks = deps.pack_chunks
    parse_audit_object = deps.parse_audit_object
    run_map_reduce = deps.run_map_reduce
    session = _session_or(session, api_key, api_url, conf)
    api_key, api_url = session.api_key, session.api_url
    if cancel_event is not None and cancel_event.is_set():
        # Consensus is unwinding (fatal sibling / Ctrl-C): a queued model can
        # race past cancel_futures into a pool worker — bill it NOTHING.
        return [], False
    a, sp, single, chunk, total = _audit_sample_prep(
        model, catalog, labeled, sys_prompt, args)
    use_cache = not a.no_cache
    cache_ttl = a.cache_ttl or CACHE_TTL_DEFAULT
    if total <= single:
        key = _audit_single_key(model, sp, labeled, a)
        if use_cache:
            cached = _cache_get(key, cache_ttl)
            if cached is not None:
                obj = parse_audit_object(cached)
                if obj is not None and isinstance(obj.get("findings"), list):
                    # only clean (ok) results are ever cached below, so a
                    # hit is a full-coverage sample — billed NOTHING.
                    return obj["findings"], True
        messages = [{"role": "system", "content": sp},
                    {"role": "user", "content": files_block(labeled)}]
        if cancel_event is not None and cancel_event.is_set():
            return [], False  # a sibling raced past cancel_futures — bill nothing
        try:
            # Hold the shared consensus gate around the single-shot call too, so
            # the global concurrency cap is ABSOLUTE (mixed single-shot + chunked
            # consensus sets can't exceed the resolved width).
            with (gate if gate is not None else contextlib.nullcontext()):
                if cancel_event is not None and cancel_event.is_set():
                    return [], False  # cancelled while waiting for a gate slot
                content, _u, _b = complete(api_key, api_url, model, messages,
                                           a, session=session)
            obj = parse_audit_object(content)
            if obj is None or not isinstance(obj.get("findings"), list):
                return [], False  # unparseable ≠ clean
            # A cap-truncated or salvaged reply parsed fine but is missing its
            # tail — consensus must not count it as full coverage.
            ok = (not obj.get("_repaired")
                  and _b.get("finish_reason") != "length"
                  and not _b.get("salvaged_partial"))
            # Cache only CLEAN same-model results (mirrors run_map_reduce):
            # a partial/repaired/fallback-served answer must never resume a
            # later run as full coverage.
            if ok and use_cache \
                    and _b.get("_served_model", model) == model:
                _cache_put(key, content)
            return obj["findings"], ok
        except ChatError as err:
            if err.category in ("key", "funds"):
                # Every subsequent call is doomed identically — falling through
                # to a full map-reduce would burn 2×N failing paid attempts
                # before reporting the real problem.
                raise
            pass  # fall through to a split across the same model
    chunk_chars = min(chunk if total > single else max(total // 3 + 1000, 20_000),
                      single)
    packed = pack_chunks(labeled, chunk_chars)
    try:
        final, partial, _r = run_map_reduce(
            api_key, api_url, model, sp, packed, a, AUDIT_SYNTH_PROMPT, single,
            reducer=findings_reducer,
            code_map=build_code_map(labeled, budget=code_map_budget(single)),
            gate=gate, cancel_event=cancel_event, session=session)
    except ChatError as err:
        if err.category in ("key", "funds"):
            raise
        return [], False
    obj = extract_json(final)
    if obj is None or not isinstance(obj.get("findings"), list):
        return [], False
    ok = (not partial and not obj.get("_unparsed_chunks")
          and not obj.get("_repaired_chunks"))
    return obj["findings"], ok


def _audit_split_estimate(catalog, model, reduce_model, labeled, total,
                          eff_total, profile, dens, max_tokens, structured,
                          fb_args=None, fb_conf=None, deps=None):
    """(n_chunks, expected, bound, assumed) — the ONE sizing/pricing helper
    shared by --dry-run and the --repo upfront plan, mirroring exactly what
    the live path will gate: single-shot when it fits, else the same
    pack_chunks split with the structured (deterministic-merge) discount.
    `fb_args`/`fb_conf`: the chunked lane's live run
    is fallback-aware, so the plan prices the same
    max(requested, candidate) figure — plan == live spend by construction. The
    single-shot lane keeps the requested-only price on purpose: it gates
    per call in complete() (main thread), exactly like the live path."""
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    build_code_map = deps.build_code_map
    code_map_budget = deps.code_map_budget
    estimate_cost = deps.estimate_cost
    estimate_cost_mr = deps.estimate_cost_mr
    estimate_cost_mr_fb = deps.estimate_cost_mr_fb
    pack_chunks = deps.pack_chunks
    single, chunk = profile.single_shot_chars, profile.chunk_chars
    if eff_total <= single:
        expected, bound, assumed = estimate_cost(
            catalog, model, total, 1, max_tokens)
        return 1, expected, bound, assumed
    packed = pack_chunks(
        labeled, max(MIN_REASONING_CHUNK, int(min(chunk, single) / dens)))
    n_chunks = len(packed)
    # The repo map is prepended to EVERY chunk's system prompt — that is
    # real billed input (map size x chunk count) the estimate must include
    # or the printed plan under-states the actual spend.
    map_chars = len(build_code_map(labeled, budget=code_map_budget(single)))
    if fb_args is not None:
        # per-chunk REAL sizes (chunk + repo map), matching the live gate's
        # threading — plan == gate stays true for the per-chunk fallback
        # reserve too (final spend-safety HIGH).
        expected, bound, assumed = estimate_cost_mr_fb(
            catalog, model, reduce_model, total + map_chars * n_chunks,
            n_chunks, max_tokens, fb_args, fb_conf,
            synthesis=not structured,
            per_call_chars=[len(c) + map_chars for c in packed])
    else:
        expected, bound, assumed = estimate_cost_mr(
            catalog, model, reduce_model, total + map_chars * n_chunks,
            n_chunks, max_tokens, synthesis=not structured)
    return n_chunks, expected, bound, assumed


def _parse_consensus_models(args, catalog, api_key, deps=None):
    """Parse + validate the --consensus model list. Runs BEFORE the --repo
    plan prints and before ANY spend: a typo'd second model must not be
    discovered after the first model's full paid audit, and
    an invalid set must never yield a plan object that prices something the
    gate will refuse. Exits on <2 models or unknown ids."""
    EXIT_USAGE = deps.EXIT_USAGE
    _fail_exit = deps._fail_exit
    difflib = deps.difflib
    usage_exit = deps.usage_exit
    models = [m.strip() for m in args.consensus.split(",") if m.strip()]
    if len(models) < 2:
        usage_exit("--consensus needs at least two models (comma-separated).")
    cat_ids = [m.get("id") for m in catalog
               if isinstance(m, dict) and m.get("id")]
    if cat_ids:
        unknown = [m for m in models if m not in cat_ids]
        if unknown:
            hints = []
            for u in unknown:
                close = difflib.get_close_matches(u, cat_ids, n=1, cutoff=0.4)
                hints.append(f"'{u}'"
                             + (f" (did you mean: {close[0]}?)" if close else ""))
            cmsg = ("unknown consensus model(s): " + ", ".join(hints)
                    + " — nothing was run or billed.")
            # a bad model id is a USAGE error — exit 64 on BOTH the
            # --json and prose paths (was: envelope exit_code 1 + a bare
            # sys.exit(string) → 1), matching every other usage refusal.
            _fail_exit(args, "consensus", "usage", cmsg,
                       exit_code=EXIT_USAGE, api_key=api_key)
    return models


def _consensus_estimate(catalog, models, labeled, total,
                        explicit_max_tokens=None, fb_args=None, fb_conf=None, deps=None):
    """(expected_sum, bound_sum, parts, per_model_chunks, assumed_any) — the
    ONE consensus pricing that backs the --repo upfront plan, computed once
    so the printed plan is accurate BY CONSTRUCTION. Each model is priced on
    ITS OWN chunking — the
    old models[0]-priced single-chunk-count estimate was ~30x off on mixed
    pairs (a small-window second model runs many more calls
    than the default model's chunking suggests) — and as MAP-ONLY work:
    consensus audits reduce with the deterministic findings_reducer (NO
    synthesis LLM call), so a chunked model costs n_chunks calls, not
    n_chunks*2, which over-priced every chunked consensus batch 2x into
    spurious spend/fleet refusals. each model is
    priced at the SAME max_tokens its live worker resolves —
    `explicit_max_tokens` is the user's --max-tokens (as the worker specs
    carry it; None = auto) run through the same with_output_budget core
    _audit_sample_prep uses, so plan == gate == live by construction. The
    old prof_m.output_budget figure under-priced any explicit budget LARGER
    than the profile default and weakened the 3x worst-case guard.
    `fb_args`/`fb_conf`: ONLY the --best-of lanes
    pass them — their samples may legally --fallback to a pricier alt, so
    each sample is priced fallback-aware (estimate_cost_fb). Consensus
    lanes must NOT pass them: their workers pin the SACRED _no_fallback, a
    swap is impossible, and the estimate stays byte-identical."""
    RequestSpec = deps.RequestSpec
    build_code_map = deps.build_code_map
    code_map_budget = deps.code_map_budget
    estimate_cost = deps.estimate_cost
    estimate_cost_fb = deps.estimate_cost_fb
    model_profile = deps.model_profile
    pack_chunks = deps.pack_chunks
    expected_sum, bound_sum, assumed_any = 0.0, 0.0, False
    parts, per_model_chunks = [], {}
    for m in models:
        prof_m = model_profile(catalog, m)
        est_input = total
        per_chars_m = None  # per-chunk REAL sizes for the fb lanes below
        if total <= prof_m.single_shot_chars:
            n_calls_m = 1
        else:
            packed_m = pack_chunks(labeled, min(prof_m.chunk_chars,
                                                prof_m.single_shot_chars))
            n_calls_m = max(1, len(packed_m))
            # each chunk also carries this model's repo map
            map_len_m = len(build_code_map(
                labeled,
                budget=code_map_budget(prof_m.single_shot_chars)))
            est_input += n_calls_m * map_len_m
            per_chars_m = [len(c) + map_len_m for c in packed_m]
        # the exact budget run_one_audit resolves for this worker
        # (_audit_sample_prep passes the same input_chars figure)
        mt_m = RequestSpec(max_tokens=explicit_max_tokens).with_output_budget(
            prof_m,
            total if total <= prof_m.single_shot_chars
            else prof_m.chunk_chars).max_tokens
        if fb_args is not None:
            # per-chunk sizes so each best-of map call's swap is reserved
            # at its OWN candidate (final spend-safety HIGH; None =
            # single-shot, where the one call IS the whole input).
            exp_m, bnd_m, assumed_m = estimate_cost_fb(
                catalog, m, est_input, n_calls_m, mt_m, fb_args, fb_conf,
                per_call_chars=per_chars_m)
        else:
            exp_m, bnd_m, assumed_m = estimate_cost(
                catalog, m, est_input, n_calls_m, mt_m)
        expected_sum += exp_m
        bound_sum += bnd_m
        assumed_any = assumed_any or assumed_m
        parts.append(m.split('/')[-1])
        # M6/M37: best-of/consensus can list the SAME model id more than once
        # (K samples). ACCUMULATE its calls — assigning overwrote earlier
        # samples so the displayed n_chunks under-counted (cost was already
        # correct via expected_sum +=).
        per_model_chunks[m] = per_model_chunks.get(m, 0) + n_calls_m
    return expected_sum, bound_sum, parts, per_model_chunks, assumed_any


def _best_of_audit_misses(catalog, model, labeled, sys_prompt, args, k,
                          explicit_max_tokens, original_max_tokens, deps=None):
    """[(miss_calls, miss_input_chars, sample_max_tokens, miss_call_chars)]
    per best-of sample — the salted cache entries the live run will NOT
    find — the cache is resolved BEFORE the run so only these cache-missing
    calls are billed (exactly like
    run_map_reduce's own resume lane). Each sample's spec/keys are built
    through the SAME helpers run_one_audit uses (_audit_sample_prep/
    _audit_single_key/_map_note), so a predicted hit here is a real hit
    there by construction. sample_max_tokens is the RESOLVED
    budget the live sample runs at (a.max_tokens) — the gate must price
    that exact figure, not the profile default, or a larger explicit
    --max-tokens under-prices the run and weakens the 3x worst-case guard.
    miss_call_chars is each billed call's REAL size (chunk + repo map) so
    the fallback-aware gate reserves each chunk's OWN candidate (final
    spend-safety HIGH)."""
    CACHE_TTL_DEFAULT = deps.CACHE_TTL_DEFAULT
    RequestSpec = deps.RequestSpec
    _CHUNK_IDX_TOKEN = deps._CHUNK_IDX_TOKEN
    _audit_sample_prep = deps._audit_sample_prep
    _audit_single_key = deps._audit_single_key
    _cache_get = deps._cache_get
    _cache_key = deps._cache_key
    _map_note = deps._map_note
    build_code_map = deps.build_code_map
    code_map_budget = deps.code_map_budget
    dataclasses = deps.dataclasses
    pack_chunks = deps.pack_chunks
    spec = RequestSpec.from_args(args)
    use_cache = not spec.no_cache
    ttl = spec.cache_ttl or CACHE_TTL_DEFAULT
    plans = []
    for i in range(k):
        # mirror _one_sample exactly — per-sample salt lane via replace
        # A5: mirror _one_sample — RAW original_max_tokens, not the clamped
        # spec.max_tokens — so this predicted cache key equals the live one.
        sa = dataclasses.replace(
            spec, _cache_salt=f"best-of:{i}",
            max_tokens=original_max_tokens if explicit_max_tokens else None)
        a, sp, single, chunk, total = _audit_sample_prep(
            model, catalog, labeled, sys_prompt, sa)
        if total <= single:
            key = _audit_single_key(model, sp, labeled, a)
            if use_cache and _cache_get(key, ttl) is not None:
                plans.append((0, 0, a.max_tokens, []))
            else:
                plans.append((1, total, a.max_tokens, [total]))
            continue
        packed = pack_chunks(labeled, min(chunk, single))
        code_map = build_code_map(labeled, budget=code_map_budget(single))
        note = _map_note(sp, code_map, len(packed))
        miss_calls, miss_input, miss_sizes = 0, 0, []
        for j, ch in enumerate(packed):
            key = _cache_key(model, note.replace(_CHUNK_IDX_TOKEN, str(j + 1)), ch,
                             a.max_tokens, a.temperature,
                             a.response_format, salt=a._cache_salt)
            if use_cache and _cache_get(key, ttl) is not None:
                continue
            miss_calls += 1
            # each billed chunk re-sends the repo map in its system prompt
            miss_input += len(ch) + len(code_map)
            miss_sizes.append(len(ch) + len(code_map))
        plans.append((miss_calls, miss_input, a.max_tokens, miss_sizes))
    return plans


def _print_repo_plan(meta, catalog, model, reduce_model, labeled, total,
                     eff_total, profile, dens, args, api_key,
                     consensus_models=None, best_of=None, explicit_mt=None,
                     conf=None, deps=None):
    """5b: the upfront `audit --repo` plan — file count, chars, chunking and
    the est. cost, BEFORE anything is sent or billed. Prose goes to stderr;
    under --format json ONE compact plan object precedes the standard audit
    envelope on stdout."""
    _audit_split_estimate = deps._audit_split_estimate
    _consensus_estimate = deps._consensus_estimate
    json = deps.json
    redact = deps.redact
    sys = deps.sys
    structured = args.format in ("json", "report")
    per_model_chunks = None
    if consensus_models:
        # Under --consensus the plan must state what the CONSENSUS gate will
        # actually charge — the SAME shared estimate the gate uses, summed
        # across the (already validated) model set — never the lone default
        # model's figure, which is not what gets gated or billed.
        expected, bound, _parts, per_model_chunks, assumed = \
            _consensus_estimate(catalog, consensus_models, labeled, total,
                                explicit_mt)
        n_chunks = sum(per_model_chunks.values())
    elif best_of:
        # Under --best-of the plan prices the FULL K-sample work
        # (_consensus_estimate over [model]*K); the live gate prices only
        # its cache-missing share — equal cold, less on a resume.
        # Fallback-aware (fb_args): best-of samples may --fallback, and the
        # live gate prices that — plan == gate.
        expected, bound, _parts, chunks_by, assumed = \
            _consensus_estimate(catalog, [model] * best_of, labeled, total,
                                explicit_mt, fb_args=args, fb_conf=conf)
        n_chunks = sum(chunks_by.values())
    else:
        n_chunks, expected, bound, assumed = _audit_split_estimate(
            catalog, model, reduce_model, labeled, total, eff_total, profile,
            dens, args.max_tokens, structured, fb_args=args, fb_conf=conf)
    # Whether the deep cross-file pass will run: default ON for --repo,
    # honoring --deep/--no-deep — but NEVER under --consensus or --best-of,
    # where corroboration replaces it. Stated in the plan
    # so the printed strategy is honest about the flags' effect.
    deep_flag = getattr(args, "deep", None)
    # Mirror the runtime guard (deep runs only when there is >1 chunk): a
    # single-shot / single-chunk repo reports deep:false so the machine plan
    # can't promise a cross-file pass that will never run.
    deep = (not getattr(args, "consensus", None) and not best_of
            and (deep_flag if deep_flag is not None else True)
            and n_chunks > 1)
    if args.format == "json":
        # No cost fields: billing is plan-dependent, so the machine plan reports
        # files/chars/chunks only — never a dollar estimate (founder policy).
        plan = {"schema_version": 1, "kind": "audit", "status": "plan",
                "repo": meta["root"], "files": meta["files"],
                "chars": meta["chars"], "git": meta["git"],
                "skipped": meta["skipped"],
                "omitted_over_cap": meta["omitted_over_cap"],
                "omitted_oversize": meta.get("omitted_oversize", 0),
                "coverage_gap": meta.get("coverage_gap", False),
                "model": ("consensus:" + ",".join(consensus_models))
                if consensus_models else model,
                "n_chunks": n_chunks,
                "deep": bool(deep)}
        if consensus_models:
            # Additive fields: name the model SET the estimate covers and
            # its per-model chunk split (n_chunks above is their sum — the
            # total map calls the gate prices).
            plan["consensus"] = list(consensus_models)
            plan["n_chunks_by_model"] = per_model_chunks
        if best_of:
            plan["best_of"] = best_of  # est covers all K samples (7a)
        print(redact(json.dumps(plan, separators=(",", ":")), api_key))
        return
    sk = meta["skipped"]
    skipped_bits = ", ".join(
        f"{sk[k]} {k}" for k in ("binary", "lockfile", "oversize", "vendored")
        if sk.get(k))
    if consensus_models:
        strategy = (f"{n_chunks} map call(s) across consensus "
                    f"{', '.join(consensus_models)}")
    elif best_of:
        strategy = (f"{n_chunks} map call(s) across {best_of} best-of "
                    f"samples on {model}")
    else:
        strategy = f"{n_chunks} chunk(s) on {model}"
    print(
        f"ambient: repo audit plan — {meta['files']} files, "
        f"{meta['chars']:,} chars "
        f"({'git-tracked' if meta['git'] else 'walked'}"
        + (f"; skipped {skipped_bits}" if skipped_bits else "") + ") → "
        f"{strategy}",
        file=sys.stderr,
    )
    if meta["omitted_over_cap"]:
        print(f"ambient: NOTE — {meta['omitted_over_cap']} file(s) over the "
              "input ceiling were EXCLUDED (explicit coverage gap)",
              file=sys.stderr)
    if meta.get("omitted_oversize"):
        print(f"ambient: NOTE — {meta['omitted_oversize']} source file(s) over "
              "the per-file ceiling were EXCLUDED (explicit coverage gap)",
              file=sys.stderr)
