"""Model-aware map-reduce fan-out and synthesis composition."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class MapReduceDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_map_reduce(api_key, api_url, model, map_system, chunks, args,
                   synth_system, reduce_budget, reducer=None, code_map="",
                   gate=None, cancel_event=None, reduce_model=None,
                   catalog=None, session=None, deps=None):
    """Fan chunk calls out in parallel, then reduce the per-chunk results.
    Returns (final_text, partial, reason). Never discards paid-for work: failed
    or truncated chunks are counted as coverage gaps and surfaced; a synthesis
    failure falls back to the raw per-chunk reports rather than exiting.
    If `reducer(chunk_texts)` is given, it replaces the LLM synthesis with a
    deterministic Python merge (A5: structured findings dedup, no re-billed
    synthesis call). `gate` (a shared threading.Semaphore, consensus lane)
    caps TOTAL concurrent complete() calls ACROSS sibling pools — without it
    N models × N chunks each = width² simultaneous network calls (bounded). A set `cancel_event` stops chunks from STARTING: once the caller
    is unwinding (fatal sibling error / Ctrl-C), no new work gets billed.
    `reduce_model` (explicit --reduce-model / AMBIENT_MODEL_MAP only)
    routes the SYNTHESIS calls to a different model — cheap map, strong
    reduce; default = the map model, byte-identical behavior. When it
    differs and a `catalog` is supplied, the synthesis is sized to the
    REDUCE model's own window the hierarchical grouping budget is
    clamped to its single-shot capacity and the synthesis call gets a
    reduce-specific replaced spec (max_tokens/response_format re-derived) — a
    smaller-window reduce model must never receive merge prompts or token
    budgets sized for the map model."""
    CACHE_TTL_DEFAULT = deps.CACHE_TTL_DEFAULT
    ChatError = deps.ChatError
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    _CHUNK_IDX_TOKEN = deps._CHUNK_IDX_TOKEN
    _cache_get = deps._cache_get
    _cache_key = deps._cache_key
    _cache_put = deps._cache_put
    _chunk_ranges = deps._chunk_ranges
    _map_note = deps._map_note
    _map_reduce_core = deps._map_reduce_core
    _reduce_response_format = deps._reduce_response_format
    _resolve_parallel = deps._resolve_parallel
    _retry_delay = deps._retry_delay
    _session_or = deps._session_or
    complete = deps.complete
    concurrent = deps.concurrent
    dataclasses = deps.dataclasses
    model_profile = deps.model_profile
    os = deps.os
    sys = deps.sys
    threading = deps.threading
    time = deps.time
    if not chunks:   # L12: no input → ThreadPoolExecutor(max_workers=0) raises
        return "", False, "no input"
    session = _session_or(session, api_key, api_url)
    api_key, api_url = session.api_key, session.api_url
    # FAN-OUT lane: the caller's batch gate already reserved any --fallback
    # swap exposure up front (fallback-aware estimates) — per-worker
    # re-gating is off (see RequestSpec.gate_fallback). Applies to the
    # synthesis spec too (derived below), which the same batch gate priced.
    spec = dataclasses.replace(RequestSpec.from_args(args),
                               gate_fallback=False)
    if cancel_event is None:
        # Local fail-fast lane: a worker-side fatal
        # must be able to stop siblings even when no caller event exists.
        cancel_event = threading.Event()
    total = sum(map(len, chunks))
    width = min(_resolve_parallel(spec), len(chunks))
    synth_model = reduce_model or model
    synth_spec = spec
    if synth_model != model and catalog:
        reduce_profile = model_profile(catalog, synth_model)
        # Pack merge inputs to the SMALLER of the two windows: shrinking for
        # a tiny reduce model is required; growing for a bigger one is not
        # (the map model's budget is already proven safe for these partials).
        reduce_budget = min(reduce_budget, reduce_profile.single_shot_chars)
        synth_spec = dataclasses.replace(
            spec,
            max_tokens=(min(spec.max_tokens, reduce_profile.output_budget)
                        if spec.max_tokens
                        else reduce_profile.output_budget),
            response_format=_reduce_response_format(
                spec.response_format, reduce_profile))
    print(
        f"ambient: input {total:,} chars → {len(chunks)} chunks → parallel calls "
        f"(max {width} at once) + {'deterministic merge' if reducer else 'synthesis'} "
        f"(~{total // 3:,} input tokens)"
        + (f" · reduce on {synth_model}" if synth_model != model else ""),
        file=sys.stderr,
    )

    map_note = _map_note(map_system, code_map, len(chunks))

    use_cache = not spec.no_cache
    cache_ttl = spec.cache_ttl or CACHE_TTL_DEFAULT
    cache_hits = [0]
    cache_hits_lock = threading.Lock()   # L14: workers increment concurrently

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    def work(i):
        text, partial, cached = _map_reduce_core.run_chunk(
            i, chunks=chunks, map_note=map_note, index_marker=_CHUNK_IDX_TOKEN,
            model=model, spec=spec, session=session, cancel_event=cancel_event,
            gate=gate, cache_key=_cache_key,
            cache_get=_cache_get, cache_put=_cache_put, cache_ttl=cache_ttl,
            complete=lambda mod, messages, request_spec, **kwargs: complete(
                api_key, api_url, mod, messages, request_spec, **kwargs),
            chat_error=ChatError, retry_delay=_retry_delay, sleep=time.sleep,
            use_cache=use_cache)
        if cached:
            with cache_hits_lock:
                cache_hits[0] += 1
        return text, partial

    try:
        results, errors, missed_ranges = _map_reduce_core.collect_fanout(
            chunks, work=work, width=width, cancel_event=cancel_event,
            chunk_ranges=_chunk_ranges,
            executor=concurrent.futures.ThreadPoolExecutor,
            as_completed=concurrent.futures.as_completed)
    except KeyboardInterrupt:
        # Cancel QUEUED chunks and exit PROMPTLY. Re-raising would hit the
        # finally's blocking pool.shutdown(wait=True) and stall exit-130 for up
        # to --timeout while an in-flight worker drains — so trip cancel and
        # os._exit(130) after flushing BOTH streams (os._exit skips the blocking
        # teardown, matching the consensus/best-of Ctrl-C lanes).
        print("\nambient: cancelling…", file=sys.stderr)
        # collect_fanout already tripped cancellation and non-blockingly
        # discarded queued work before it re-raised this interrupt.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    if cache_hits[0]:
        print(f"ambient: {cache_hits[0]}/{len(chunks)} chunks served from cache "
              "(not re-billed)", file=sys.stderr)
    done = [(i, r[0]) for i, r in enumerate(results) if r]
    truncated = [i + 1 for i, r in enumerate(results) if r and r[1]]
    for i, r in enumerate(results):
        if r and r[1]:
            missed_ranges.extend(_chunk_ranges(chunks[i]))
    if not done:
        raise ChatError(
            "stall",
            f"all {len(chunks)} chunk calls failed — first: "
            f"{errors[0] if errors else 'unknown'}",
        )

    gap = _map_reduce_core.coverage_gap(errors, truncated)

    def merge(texts):
        return _map_reduce_core.synthesize_parts(
            texts, system=synth_system, gap=gap, model=synth_model,
            spec=synth_spec, session=session,
            complete=lambda mod, messages, request_spec, **kwargs: complete(
                api_key, api_url, mod, messages, request_spec, **kwargs),
            recoverable_errors=(ChatError, NetworkError))

    texts = [r for _, r in done]
    # Deterministic Python reduce (structured mode): no LLM synthesis, no
    # re-billed merge call — just parse + dedupe the chunk findings.
    if reducer is not None:
        final = reducer(texts)
        partial, reason = _map_reduce_core.partial_reason(
            errors=errors, truncated=truncated, synth_failed=False,
            missed_ranges=missed_ranges, chunk_count=len(chunks))
        if partial:
            print(f"[ambient-codex map-reduce: PARTIAL — {reason}]",
                  file=sys.stderr)
        return final, partial, reason
    # The synthesis prompt is system + gap + per-part headers, not just the
    # parts — reserve that overhead so a merge call can't blow the context.
    overhead = len(synth_system) + len(gap) + 200 * max(1, len(texts))
    # No 20k floor: a small-window model (e.g. gemma, single≈17.6k) would then
    # get a merge budget above its own window → doomed, re-billed synthesis
    # calls. Respect the model's real budget.
    effective_budget = max(1_000, reduce_budget - overhead)
    final, synth_failed = _map_reduce_core.hierarchical_reduce(
        texts, effective_budget=effective_budget, merge=merge)
    partial, reason = _map_reduce_core.partial_reason(
        errors=errors, truncated=truncated, synth_failed=synth_failed,
        missed_ranges=missed_ranges, chunk_count=len(chunks))
    if partial:
        print(
            f"[ambient-codex map-reduce: PARTIAL — {reason}]",
            file=sys.stderr,
        )
    return final, partial, reason
