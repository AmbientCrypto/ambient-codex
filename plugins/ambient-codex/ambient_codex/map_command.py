"""Independent multi-item map command orchestration."""

import argparse
import concurrent.futures
import os
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class MapDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_map(args, api_key, api_url, conf, deps):
    threading = deps.threading
    CACHE_TTL_DEFAULT = deps.CACHE_TTL_DEFAULT
    ChatError = deps.ChatError
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    EXIT_USAGE = deps.EXIT_USAGE
    MAP_OVERSIZE_MSG = deps.MAP_OVERSIZE_MSG
    NetworkError = deps.NetworkError
    Session = deps.Session
    _cache_get = deps._cache_get
    _cache_key = deps._cache_key
    _cache_put = deps._cache_put
    _emit_map_result = deps._emit_map_result
    _fail_exit = deps._fail_exit
    _map_gather_items = deps._map_gather_items
    _map_workflow = deps._map_workflow
    _resolve_parallel = deps._resolve_parallel
    _retry_delay = deps._retry_delay
    apply_output_budget = deps.apply_output_budget
    complete = deps.complete
    density_factor = deps.density_factor
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    refuse_if_secrets = deps.refuse_if_secrets
    route_model = deps.route_model
    prompt = (args.prompt or "").strip()
    if not prompt:
        _fail_exit(args, "map", "usage",
                   'map needs a per-item instruction: ambient-codex map "<prompt>" '
                   "[FILES...] (or pipe one item per line on stdin)",
                   exit_code=EXIT_USAGE, api_key=api_key)
    items = _map_gather_items(args)
    if not items:
        _fail_exit(args, "map", "usage",
                   "no items to map — pass file paths (one item per file) or "
                   "pipe one item per line on stdin (--jsonl for JSON objects "
                   'with an "input" field)',
                   exit_code=EXIT_USAGE, api_key=api_key)
    refuse_if_secrets(
        [("prompt", prompt)] + [(str(i), t) for i, t, e in items if e is None],
        getattr(args, "allow_secrets", False))
    if getattr(args, "model", None):
        note_if_hidden(args.model, conf)
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    catalog = session.catalog()  # memoized: ONE fetch for the whole command
    # `map`'s unit of fit is the LARGEST single item (each item is its own
    # single-shot call). AMBIENT_MODEL_MAP's dedicated "map" phase lets the
    # user route the whole bulk lane to a cheap model without touching chat.
    biggest = max((len(prompt) + len(t) for _i, t, e in items if e is None),
                  default=0)
    model = route_model(args, conf, "chat", catalog, input_chars=biggest,
                        phase="map")
    profile = model_profile(catalog, model)
    single = profile.single_shot_chars

    # Partition: per-item pre-errors (directory/binary/oversized/bad JSONL)
    # never bill and never block the runnable siblings. An item past the
    # model's single-shot window is refused PER-ITEM — never silently
    # truncated (that would answer over half an input and look authoritative).
    runnable, pre_errors = [], []
    for item_id, text, err in items:
        if err is not None:
            pre_errors.append((item_id, "input", err))
            continue
        eff = int((len(prompt) + len(text)) * density_factor(text))
        if eff > single:
            pre_errors.append((item_id, "input", MAP_OVERSIZE_MSG))
            continue
        runnable.append((item_id, text, eff))
    fails = len(pre_errors)
    ok = cached_n = 0
    use_cache = not getattr(args, "no_cache", False)
    cache_ttl = getattr(args, "cache_ttl", None) or CACHE_TTL_DEFAULT
    cached_hits, to_run = [], []
    explicit_budget = getattr(args, "max_tokens", None) is not None
    if runnable and explicit_budget:
        # An explicit --max-tokens applies to EVERY item (and is part of
        # every key): clamp + warn ONCE on the shared args, then each item
        # copy inherits the resolved value.
        apply_output_budget(args, profile)
    if runnable:
        # Budget EACH ITEM INDEPENDENTLY from its OWN density-adjusted size
        # (A1 right-sizing, per item) on a private args copy — so its cache
        # key depends only on (model, prompt, item, its-own-budget, temp,
        # response_format) and is stable no matter what else joins the batch.
        # Sizing once to the batch max would re-key EVERY item when a larger
        # item is added, re-billing already-cached work; it also over-budgeted
        # small items. The per-item copy also keeps complete()'s budget
        # escalation from mutating a sibling's args mid-flight.
        # Cache is resolved after the budget — the key includes max_tokens —
        # so a resumed / fully-cached re-run makes zero calls.
        for item_id, text, eff in runnable:
            item_args = argparse.Namespace(**vars(args))
            # FAN-OUT worker: the batch gate below reserves any --fallback
            # swap exposure up front (fallback-aware estimate) — no
            # per-worker fallback re-gate (see RequestSpec.gate_fallback).
            item_args.gate_fallback = False
            if not explicit_budget:
                apply_output_budget(item_args, profile, eff)
            key = _cache_key(model, prompt, text, item_args.max_tokens,
                             args.temperature,
                             getattr(args, "response_format", None))
            hit = _cache_get(key, cache_ttl) if use_cache else None
            if hit is not None:
                cached_hits.append((item_id, hit))
            else:
                to_run.append((item_id, text, key, item_args))
    if to_run:
        print(f"ambient-codex map: {len(items)} item(s), {len(to_run)} to run"
              + (f" ({len(cached_hits)} cached)" if cached_hits else "")
              + (f" ({fails} refused up front)" if fails else "")
              + f" — one {model} call per item", file=sys.stderr)
    for item_id, category, diagnosis in pre_errors:
        _emit_map_result(args, api_key, item_id,
                         category=category, diagnosis=diagnosis)
    # D7 resume: cache hits still emit their envelopes — for free.
    for item_id, hit in cached_hits:
        ok += 1
        cached_n += 1
        _emit_map_result(args, api_key, item_id, content=hit, cached=True)

    width = min(_resolve_parallel(args), max(1, len(to_run)))
    # shape: a shared gate around the network call + a cancel_event
    # flipped on the first fatal failure so no queued item starts billing
    # while the batch unwinds (re-checked after acquiring any gate slot).
    gate = threading.Semaphore(width)
    cancel_event = threading.Event()

    def work(item_id, text, key, item_args):
        return _map_workflow.run_map_item(
            item_id=item_id, text=text, key=key, item_args=item_args,
            prompt=prompt, api_key=api_key, api_url=api_url, model=model,
            session=session, gate=gate, cancel_event=cancel_event,
            complete=complete, cache_put=_cache_put, retry_delay=_retry_delay,
            sleep=time.sleep, chat_error=ChatError, network_error=NetworkError,
            use_cache=use_cache)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=width)
    aborted = False

    def _abort():
        nonlocal aborted
        aborted = True
        cancel_event.set()
        try:
            pool.shutdown(wait=False, cancel_futures=True)  # py3.9+
        except TypeError:
            pool.shutdown(wait=False)                       # py3.8

    try:
        futs = {pool.submit(work, i, t, k, ia): i for i, t, k, ia in to_run}
        for fut in concurrent.futures.as_completed(futs):
            item_id = futs[fut]
            try:
                text, partial = fut.result()
            except (ChatError, NetworkError) as err:
                if isinstance(err, NetworkError) \
                        or getattr(err, "category", "") in ("key", "funds"):
                    # Every sibling is doomed identically — cancel the queue,
                    # stop billing, and surface the REAL problem now. main()
                    # renders it as the --json error envelope / prose, exit 1.
                    _abort()
                    raise
                fails += 1
                _emit_map_result(
                    args, api_key, item_id,
                    category=getattr(err, "category", "network"),
                    diagnosis=getattr(err, "diagnosis", None) or str(err))
                continue
            except Exception as err:  # noqa: BLE001 — one item must never
                # abort the batch; record it as a failed item and keep going.
                fails += 1
                _emit_map_result(args, api_key, item_id, category="internal",
                                 diagnosis=f"{type(err).__name__}: {err}")
                continue
            except BaseException:
                # a worker-side fatal (SystemExit &
                # co) cancels the whole batch — siblings must stop billing.
                _abort()
                raise
            if partial:
                fails += 1
            else:
                ok += 1
            _emit_map_result(args, api_key, item_id, content=text,
                             partial=partial)
    except KeyboardInterrupt:
        print("\nambient: cancelling map…", file=sys.stderr)
        _abort()
        # A real Ctrl-C must end the PROCESS now: the pool's worker threads
        # are non-daemon, so re-raising would leave the interpreter joining
        # the in-flight complete() at exit — stalling exit-130 for up to
        # --timeout. os._exit skips interpreter teardown (no buffered-stream
        # flushing), so flush BOTH streams first — no emitted envelope or
        # diagnostic may be lost. Normal (non-interrupt) exits are untouched.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    finally:
        # After an abort the queue is already cancelled — a second BLOCKING
        # shutdown would sit there draining in-flight calls, turning Ctrl-C
        # exit-130 from prompt into minutes.
        if not aborted:
            pool.shutdown(wait=True)
    print(f"ambient-codex map: {ok} ok / {fails} failed / {cached_n} cached "
          f"(of {len(items)} item(s))", file=sys.stderr)
    if fails and not getattr(args, "allow_partial", False):
        sys.exit(EXIT_PARTIAL)


AUDIT_FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string",
                                 "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                    "confidence": {"type": "string", "enum": ["HIGH", "LOW"]},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "title": {"type": "string"},
                    "defect": {"type": "string"},
                    "scenario": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "confidence", "file", "line", "title",
                             "defect", "scenario", "fix"],
            },
        },
        "verdict": {"type": "string",
                    "enum": ["SHIP", "FIX FIRST", "NEEDS WORK"]},
    },
    "required": ["findings", "verdict"],
}

# Appended to the system prompt on the json_object / prompt-only paths (models
# without strict json_schema need the shape spelled out).
AUDIT_JSON_INSTRUCTION = (
    "\n\nOUTPUT FORMAT: return ONLY a JSON object (no prose, no markdown fences) "
    'matching: {"findings":[{"severity":"CRITICAL|HIGH|MEDIUM|LOW",'
    '"confidence":"HIGH|LOW","file":str,"line":int,"title":str,"defect":str,'
    '"scenario":str,"fix":str}],"verdict":"SHIP|FIX FIRST|NEEDS WORK"}. '
    "Empty findings + verdict SHIP if the code is sound."
)


AUDIT_SYNTH_PROMPT = """You are merging partial audit reports produced from chunks
of ONE codebase. Combine them into a single report: dedupe identical findings (same
file:line + same defect) keeping the highest severity and richest scenario, keep the
most severe first, preserve each finding's SEVERITY, confidence, and exact file:line
(the numbers come from absolute gutters — keep them verbatim), and drop findings that
are clearly artifacts of a file being split across chunks unless corroborated. Do not
invent findings not present in the inputs. End with ONE overall verdict
(SHIP / FIX FIRST / NEEDS WORK). If a coverage-gap note is present, state it plainly
at the TOP and do not issue a clean/SHIP verdict."""




__all__ = ("MapDependencies", "run_map")
