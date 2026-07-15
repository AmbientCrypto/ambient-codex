"""Direct ask, best-of, and multi-model consensus orchestration."""

import collections
import concurrent.futures
import dataclasses
import difflib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AskDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_ask(args, api_key, api_url, conf, deps):
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    ChatError = deps.ChatError
    Session = deps.Session
    _StreamRedactor = deps._StreamRedactor
    _ask_consensus = deps._ask_consensus
    _best_of_chat = deps._best_of_chat
    _fail = deps._fail
    _resolve_best_of = deps._resolve_best_of
    apply_output_budget = deps.apply_output_budget
    complete = deps.complete
    density_factor = deps.density_factor
    emit_json = deps.emit_json
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    pack_chunks = deps.pack_chunks
    read_stdin_if_piped = deps.read_stdin_if_piped
    redact = deps.redact
    refuse_if_secrets = deps.refuse_if_secrets
    render_result = deps.render_result
    resolve_reduce_model = deps.resolve_reduce_model
    route_model = deps.route_model
    run_map_reduce = deps.run_map_reduce
    usage_exit = deps.usage_exit
    warn_if_stdin_ignored = deps.warn_if_stdin_ignored
    # '-' in the prompt args means "also read stdin" (e.g. cat big.txt |
    # ambient-codex ask "summarize" -). Otherwise stdin is only read when no prompt
    # was given — a wrapper holding stdin open (backgrounded shells, cron)
    # must never block us.
    words = [w for w in args.prompt if w != "-"]
    want_stdin = "-" in args.prompt or not words
    question = " ".join(words).strip()
    doc = read_stdin_if_piped().strip() if want_stdin else ""
    if not want_stdin:
        warn_if_stdin_ignored("add '-' to the prompt to include piped data")
    if not question and not doc:
        usage_exit("nothing to ask (pass a prompt or pipe stdin)")
    scan = [("prompt", question)] + ([("stdin", doc)] if doc else [])
    if getattr(args, "system", None):  # --system is sent to the network too
        scan.append(("system", args.system))
    refuse_if_secrets(scan, getattr(args, "allow_secrets", False))
    if getattr(args, "model", None):
        note_if_hidden(args.model, conf)
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    catalog = session.catalog()  # memoized: ONE fetch for the whole command
    # 7a/7b are distinct corroboration lanes — combining them would fan out
    # K×N calls for no defined semantics. Validated before any spend.
    best_of_k = _resolve_best_of(args)
    if getattr(args, "consensus", None):
        if best_of_k:
            usage_exit("--consensus and --best-of cannot be combined — "
                       "pick one corroboration lane")
        _ask_consensus(args, api_key, api_url, conf, catalog, question, doc,
                       session=session)
        return
    input_size = len(question) + (len(doc) if doc else 0)
    # advisory routing: expands an explicit `-m auto` (printed) or
    # prints the readiness/fit hint — a concrete model is NEVER changed.
    model = route_model(args, conf, "chat", catalog, input_chars=input_size)
    reduce_model = resolve_reduce_model(args, conf, model, catalog=catalog)
    profile = model_profile(catalog, model)
    sizing = (input_size if input_size <= profile.single_shot_chars
              else profile.chunk_chars)
    apply_output_budget(args, profile, sizing)
    single, chunk = profile.single_shot_chars, profile.chunk_chars
    want_json = getattr(args, "json", False)

    def split_over_doc():
        q = question or "Summarize the essential content."
        base = args.system or "You are a careful, precise analyst."
        # Token-dense text (CJK ~1-1.5 chars/token vs code ~3.2) must get
        # proportionally smaller chunks or every chunk 400s on context.
        chunk_eff = max(MIN_REASONING_CHUNK,
                        int(chunk / density_factor(doc)))
        packed = pack_chunks([("input", doc)], chunk_eff)
        final, partial, reason = run_map_reduce(
            api_key, api_url, model,
            f"{base}\nQUESTION: {q}\nFrom the given chunk, extract everything "
            "relevant and give a partial answer based on THIS chunk alone.",
            packed, args,
            f"You are merging partial answers to: {q}\nCombine into one final, "
            "coherent, non-repetitive answer; resolve conflicts explicitly.",
            single, reduce_model=reduce_model, catalog=catalog,
            session=session,
        )
        if want_json:  # keep --json machine-readable even on the map-reduce path
            emit_json("ask", model=model, api_key=api_key, content=final,
                      partial=partial, reason=reason or None,
                      allow_partial=getattr(args, "allow_partial", False))
            return
        render_result(final, partial, reason, args, api_key)

    if not doc and len(question) > single:
        # No-refusal invariant: a giant argv prompt (ambient-codex ask "$(cat big.md)")
        # flows into the same split machinery as piped input instead of dying
        # on a context 400 after billing the attempt.
        doc, question = question, (
            "Answer the request contained in the document (any instructions "
            "are embedded within it).")
    eff_total = int((len(question) + len(doc)) * density_factor(doc or question))
    if doc and eff_total > single:
        if best_of_k:
            # K independent samples of a map-reduce would K× every chunk with
            # no per-sample selection semantics — refuse honestly up front.
            usage_exit("--best-of needs a single-shot-sized input — this one "
                       "requires map-reduce splitting; shrink the input or "
                       "drop --best-of")
        split_over_doc()
        return
    prompt = f"{question}\n\n{doc}".strip() if doc else question
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": prompt})
    if best_of_k:
        # 7a: K independent samples + deterministic selection (its own gate,
        # streaming is per-sample-concurrent so the live-stream lane is off).
        _best_of_chat(api_key, api_url, model, messages, args, best_of_k,
                      catalog, conf, kind="ask", session=session)
        return
    # live-stream the answer to stdout on an interactive terminal (not --raw,
    # not --json). Reasoning shows as heartbeats first, then the answer streams.
    live_stream = sys.stdout.isatty() and not args.raw and not want_json
    streamed_parts = []
    _sr = _StreamRedactor(api_key)   # M43: boundary-split-safe streaming redaction

    def on_delta(piece):
        out = _sr.feed(piece)
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
        streamed_parts.append(piece)

    try:
        content, usage, body = complete(
            api_key, api_url, model, messages, args,
            on_delta=(on_delta if live_stream else None), session=session)
    except ChatError as err:
        if streamed_parts:
            sys.stdout.write(_sr.flush())
            sys.stdout.write("\n")
            sys.stdout.flush()
        if err.category in ("empty", "context") and len(prompt) > 20_000:
            if not doc:  # token-dense argv prompt that overflowed anyway
                doc, question = prompt, (
                    "Answer the request contained in the document (any "
                    "instructions are embedded within it).")
            print(
                f"ambient: '{model}' couldn't finish in one pass — splitting the "
                "document for the same model",
                file=sys.stderr,
            )
            split_over_doc()
            return
        _fail(args, "ask", err, api_key)
    if streamed_parts:          # M43: emit the buffered streaming tail on success
        sys.stdout.write(_sr.flush())
        sys.stdout.flush()
    if want_json:
        served = body.get("_served_model", model)
        emit_json(
            "ask", model=served, api_key=api_key, content=content,
            partial=bool(body.get("salvaged_partial")),
            reason="output salvaged" if body.get("salvaged_partial") else None,
            usage=body.get("usage"), finish_reason=body.get("finish_reason"),
            extra=({"requested_model": model} if served != model else None),
            allow_partial=getattr(args, "allow_partial", False))
        return
    if args.raw:
        print(redact(json.dumps(body, indent=2), api_key))
        return
    # Only treat it as streamed if what we streamed IS the final content — a
    # stall-retry/escalation restart yields fresh content and must be reprinted.
    clean_stream = bool(streamed_parts) and "".join(streamed_parts).strip() == content
    if not clean_stream and streamed_parts:
        sys.stdout.write("\n[ambient: stream restarted — full result below]\n")
        sys.stdout.flush()
    render_result(
        content,
        bool(body.get("salvaged_partial")) or body.get("finish_reason") == "length",
        "output was salvaged/truncated", args, api_key, usage,
        # SERVED model, not requested — a pricier --fallback switch must not
        # over-state the receipt's saving.
        body.get("_served_model", model),
        already_streamed=clean_stream,
    )


# --------------------------------------------------------------------------
# Quality from cheapness. `--best-of K` buys quality with K
# cheap independent samples (one up-front gate, salted cache lanes, honest
# deterministic selection); `ask --consensus A,B` triangulates one question
# across an EXPLICIT model set (model choice SACRED) and reports agreement.

BEST_OF_MAX = 8            # sanity clamp on --best-of K
BEST_OF_TEMPERATURE = 0.7  # diversity floor when the user asked for temp 0
BEST_OF_WEAK_TEMP = 0.3    # at/below this, samples barely differ — say so


def resolve_best_of(args, deps):
    usage_exit = deps.usage_exit
    """Validated K for --best-of (None when off). K<2 is a usage error; K is
    clamped to BEST_OF_MAX. Sampling at temperature 0 would draw K identical
    answers — bump to BEST_OF_TEMPERATURE with a printed note (the user's 0
    bought determinism, which best-of by definition forgoes). A LOW non-zero
    temperature is the user's explicit choice and is KEPT, but the weak-
    corroboration tradeoff is disclosed: near-identical samples make the
    majority/similarity vote self-confirming rather than corroborating."""
    k = getattr(args, "best_of", None)
    if k is None:
        return None
    if k < 2:
        usage_exit("--best-of needs K >= 2 (K independent samples)")
    if k > BEST_OF_MAX:
        print(f"ambient: --best-of {k} capped at {BEST_OF_MAX}",
              file=sys.stderr)
        k = BEST_OF_MAX
    if getattr(args, "temperature", None) == 0:
        print(
            f"ambient: --best-of needs sampling diversity — raising "
            f"temperature 0 to {BEST_OF_TEMPERATURE}",
            file=sys.stderr,
        )
        args.temperature = BEST_OF_TEMPERATURE
    elif 0 < (getattr(args, "temperature", None) or 0) <= BEST_OF_WEAK_TEMP:
        print(
            f"ambient: note — best-of at temperature {args.temperature} "
            "draws near-identical samples, so corroboration is WEAK "
            "(self-confirming, not independent); pass --temperature "
            f"{BEST_OF_TEMPERATURE} for real diversity",
            file=sys.stderr,
        )
    return k


def select_best_sample(texts):
    """(index, method, note) — deterministic, honest selection among K
    samples. Short answers: exact-normalized majority vote when 2+ agree.
    Otherwise the pairwise-similarity centroid (self-consistency proxy):
    the sample most similar to all the others, clipped for cost. No LLM
    judge — the note states exactly what was done."""
    if len(texts) == 1:
        return 0, "single", "only one sample available"
    norm = [re.sub(r"\s+", " ", t.strip().lower()) for t in texts]
    if all(len(n) <= 120 for n in norm):
        top, votes = collections.Counter(norm).most_common(1)[0]
        if votes >= 2:
            idx = norm.index(top)
            return idx, "majority", f"{votes}/{len(texts)} samples agree"
    clipped = [t[:20_000] for t in texts]
    best_i, best_score = 0, -1.0
    for i, a in enumerate(clipped):
        score = sum(
            difflib.SequenceMatcher(None, a, b).ratio()
            for j, b in enumerate(clipped) if j != i
        ) / (len(clipped) - 1)
        if score > best_score:
            best_i, best_score = i, score
    return best_i, "similarity", (
        f"most representative of {len(texts)} samples "
        f"(mean pairwise similarity {int(best_score * 100)}%)")


def run_best_of_chat(api_key, api_url, model, messages, args, k, catalog, conf,
                     kind, session, deps):
    CACHE_TTL_DEFAULT = deps.CACHE_TTL_DEFAULT
    ChatError = deps.ChatError
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    _as_pos_int = deps._as_pos_int
    _cache_get = deps._cache_get
    _cache_key = deps._cache_key
    _cache_put = deps._cache_put
    _resolve_parallel = deps._resolve_parallel
    _session_or = deps._session_or
    complete = deps.complete
    emit_json = deps.emit_json
    redact = deps.redact
    render_result = deps.render_result
    select_best_sample = deps.select_best_sample
    """7a for ask/code: draw K INDEPENDENT single-shot samples of `messages`
    (shared gate + cancel_event fan-out, per-sample salted cache so re-runs
    resume), then emit the K candidates plus a deterministic selection. The
    salted cache is resolved BEFORE any call, so only cache-missing samples
    are drawn."""
    session = _session_or(session, api_key, api_url, conf)
    api_key, api_url = session.api_key, session.api_url
    spec = RequestSpec.from_args(args)
    want_json = spec.json
    system_text = "".join(m.get("content") or "" for m in messages
                          if m.get("role") == "system")
    user_text = "\n".join(m.get("content") or "" for m in messages
                          if m.get("role") != "system")
    rf = spec.response_format
    use_cache = not spec.no_cache
    ttl = spec.cache_ttl or CACHE_TTL_DEFAULT
    keys = [_cache_key(model, system_text, user_text, spec.max_tokens,
                       spec.temperature, rf, salt=f"best-of:{i}")
            for i in range(k)]
    samples = {}
    to_run = []
    for i, key in enumerate(keys):
        hit = _cache_get(key, ttl) if use_cache else None
        if hit is not None:
            # cache entries are only ever written for the REQUESTED model
            # (served == model below), so a hit's served model is `model`.
            samples[i] = {"index": i, "content": hit, "partial": False,
                          "cached": True, "served_model": model}
        else:
            to_run.append((i, key))
    errors = []
    usage_tot = {"prompt_tokens": 0, "completion_tokens": 0}
    # token counts are also kept PER SERVED MODEL — with
    # --fallback a sample can be served by a different (pricier) model,
    # and pricing the aggregate at the selected sample's model would
    # over-state the saving. The receipt/JSON price each model's own
    # tokens instead.
    usage_by_served = {}
    est_any = saw_usage = False
    if to_run:
        n = len(to_run)
        print(f"ambient: best-of {k} — drawing {n} sample(s)"
              + (f" ({k - n} cached, not re-billed)" if k - n else "")
              + f" on {model} at temperature {spec.temperature}",
              file=sys.stderr)
        width = min(_resolve_parallel(spec), n)
        gate = threading.Semaphore(width)
        cancel_event = threading.Event()
        # FAN-OUT workers: the batch gate above already reserved any
        # --fallback swap exposure (fallback-aware estimate) — no per-worker
        # fallback re-gate (see RequestSpec.gate_fallback).
        wspec = dataclasses.replace(spec, gate_fallback=False)

        def work(i, key):
            if cancel_event.is_set():
                raise ChatError("cancelled",
                                "best-of cancelled before this sample started")
            with gate:
                if cancel_event.is_set():
                    raise ChatError("cancelled",
                                    "best-of cancelled while waiting for a slot")
                try:
                    # The spec is FROZEN — complete()'s budget escalation
                    # rides its own replaced copy, so sibling samples can
                    # never see each other's budget (no per-sample clone).
                    out, usage, body = complete(
                        api_key, api_url, model, messages, wspec,
                        session=session)
                except NetworkError:
                    cancel_event.set()  # fatal for every sibling — stop billing
                    raise
                except ChatError as err:
                    if err.category in ("key", "funds"):
                        cancel_event.set()  # same zero-race fail-fast as map
                    raise
            partial = (bool(body.get("salvaged_partial"))
                       or body.get("finish_reason") == "length")
            served = body.get("_served_model", model)
            if use_cache and not partial and served == model:
                _cache_put(key, out)
            return out, partial, usage, served

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
            futs = {pool.submit(work, i, key): i for i, key in to_run}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    out, partial, usage, served = fut.result()
                except (ChatError, NetworkError) as err:
                    if isinstance(err, NetworkError) \
                            or getattr(err, "category", "") in ("key", "funds"):
                        _abort()
                        raise  # every sibling is doomed identically
                    errors.append((i, getattr(err, "category", "network"),
                                   getattr(err, "diagnosis", None) or str(err)))
                    continue
                except BaseException:
                    # any worker-side fatal (incl.
                    # SystemExit) fails the batch FAST — no sibling may keep
                    # billing while the unwind propagates.
                    _abort()
                    raise
                if served != model:
                    # H4 disclosure: fallback stayed available on this lane
                    # (a lone best-of model is not an explicit SET), but a
                    # swap must never be silent — name requested → served.
                    print(f"ambient: best-of sample {i + 1} was served by "
                          f"'{served}' (requested '{model}' — --fallback)",
                          file=sys.stderr)
                samples[i] = {"index": i, "content": out, "partial": partial,
                              "cached": False, "served_model": served}
                if usage:
                    saw_usage = True
                    tin = _as_pos_int(usage.get("prompt_tokens"), 0)
                    tout = _as_pos_int(usage.get("completion_tokens"), 0)
                    usage_tot["prompt_tokens"] += tin
                    usage_tot["completion_tokens"] += tout
                    per = usage_by_served.setdefault(
                        served, {"prompt_tokens": 0, "completion_tokens": 0})
                    per["prompt_tokens"] += tin
                    per["completion_tokens"] += tout
                    if usage.get("_estimated"):
                        per["_estimated"] = True
                    est_any = est_any or bool(usage.get("_estimated"))
        except KeyboardInterrupt:
            print("\nambient: cancelling best-of…", file=sys.stderr)
            _abort()
            # Match cmd_map: non-daemon pool workers are joined by
            # concurrent.futures' atexit at shutdown, so re-raising would stall
            # exit-130 for up to --timeout if a sibling is mid-call. _abort()
            # set aborted=True so the finally's blocking shutdown is skipped;
            # os._exit after flushing both streams.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(130)
        finally:
            if not aborted:
                pool.shutdown(wait=True)
    if not samples:
        cat, diag = ((errors[0][1], errors[0][2]) if errors
                     else ("empty", "no sample produced output"))
        raise ChatError(cat, f"all {k} best-of samples failed — first: {diag}")
    ordered = [samples[i] for i in sorted(samples)]
    best_i, method, note = select_best_sample([s["content"] for s in ordered])
    best = ordered[best_i]
    best_served = best.get("served_model", model)
    usage_out = None
    if saw_usage and usage_tot["prompt_tokens"] + usage_tot["completion_tokens"]:
        usage_out = dict(usage_tot)
        if est_any:
            usage_out["_estimated"] = True
    # H1 honesty: a run with FAILED samples is a degraded pick — the result
    # is PARTIAL (exit 2 unless --allow-partial), and the coverage reason
    # names exactly which samples failed and why.
    fail_note = None
    if errors:
        fail_note = (f"{len(errors)}/{k} sample(s) failed: " + "; ".join(
            f"sample {i + 1} [{c}]: {redact(d, api_key)}"
            for i, c, d in sorted(errors)))
        print(f"ambient: {len(errors)}/{k} sample(s) failed — selection ran "
              f"over the {len(ordered)} that succeeded", file=sys.stderr)
    partial = best["partial"] or bool(errors)
    reason_bits = []
    if best["partial"]:
        reason_bits.append("selected sample was salvaged/truncated")
    if fail_note:
        reason_bits.append(fail_note)
    reason = "; ".join(reason_bits) or None
    if want_json:
        emit_json(
            kind, model=best_served, api_key=api_key, content=best["content"],
            partial=partial, reason=reason, usage=usage_out,
            allow_partial=spec.allow_partial,
            extra={
                "best_of": k,
                "selected_index": best["index"],
                "selection": {"method": method, "note": note},
                "candidates": [
                    {"index": s["index"], "content": s["content"],
                     "partial": s["partial"], "cached": s["cached"],
                     "served_model": s.get("served_model", model)}
                    for s in ordered],
                # SAME shape on every surface: a list of
                # {index, category, diagnosis} + an additive count.
                "failed_samples": [
                    {"index": i, "category": c, "diagnosis": redact(d, api_key)}
                    for i, c, d in sorted(errors)],
                "failed_sample_count": len(errors),
                # the per-SERVED-model token split — the ONLY
                # honest basis for pricing a mixed-served run (a fallback
                # sample bills at ITS model's price, not the selected one's).
                **({"usage_by_served_model": {
                        m: dict(u)
                        for m, u in sorted(usage_by_served.items())}}
                   if usage_by_served else {}),
                **({"requested_model": model}
                   if best_served != model else {}),
            })
        return
    for s in ordered:
        tag = (" (cached)" if s["cached"] else "") \
            + (" [PARTIAL]" if s["partial"] else "") \
            + (f" (served by {s['served_model']})"
               if s.get("served_model", model) != model else "")
        print(redact(f"--- best-of sample {s['index'] + 1}/{k}{tag} ---",
                     api_key))
        print(redact(s["content"], api_key) + "\n")
    print(redact(f"=== best-of selection: sample {best['index'] + 1} "
                 f"({method}: {note}) ===", api_key))
    render_result(best["content"], partial,
                  reason or "selected sample was salvaged/truncated",
                  spec, api_key, usage_out, best_served,
                  usage_by_model=usage_by_served or None)


def answers_agreement(texts):
    """(level, mean_ratio, note) — TEXTUAL-similarity agreement across
    consensus answers. Deliberately humble: it measures how alike the words
    are (deterministic, stdlib difflib), it does not verify semantics — the
    note says so, and divergence is surfaced loudly."""
    if len(texts) < 2:
        return "n/a", 0.0, "fewer than two answers — nothing to compare"
    clipped = [re.sub(r"\s+", " ", t.strip().lower())[:20_000] for t in texts]
    ratios = [difflib.SequenceMatcher(None, clipped[i], clipped[j]).ratio()
              for i in range(len(clipped)) for j in range(i + 1, len(clipped))]
    mean = sum(ratios) / len(ratios)
    pct = int(mean * 100)
    if mean >= 0.8:
        return "high", mean, (
            f"answers are textually very similar ({pct}%) — the models "
            "broadly agree (similarity of wording, not a semantic proof)")
    if mean >= 0.5:
        return "medium", mean, (
            f"answers partially overlap (textual similarity {pct}%) — "
            "compare the differing details before trusting either")
    return "low", mean, (
        f"answers DIVERGE (textual similarity {pct}%) — verify "
        "independently before trusting any single one")


def run_ask_consensus(args, api_key, api_url, conf, catalog, question, doc,
                      session, deps):
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    ChatError = deps.ChatError
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    _answers_agreement = deps._answers_agreement
    _parse_consensus_models = deps._parse_consensus_models
    _resolve_parallel = deps._resolve_parallel
    _session_or = deps._session_or
    complete = deps.complete
    density_factor = deps.density_factor
    emit_json = deps.emit_json
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    paint = deps.paint
    redact = deps.redact
    savings_note = deps.savings_note
    usage_exit = deps.usage_exit
    """7b: run the SAME ask on several EXPLICITLY-named models (--consensus —
    the set is the user's choice, SACRED) concurrently, then print every
    model's answer plus an agreement/divergence note. Fail-fast semantics
    mirror audit --consensus: key/funds/
    network abort the whole set; any other per-model failure is reported and
    makes the result PARTIAL, never silently dropped."""
    session = _session_or(session, api_key, api_url, conf)
    api_key, api_url = session.api_key, session.api_url
    spec = RequestSpec.from_args(args)
    models = _parse_consensus_models(spec, catalog, api_key)
    prompt = f"{question}\n\n{doc}".strip() if doc else question
    want_json = spec.json
    explicit_max = spec.max_tokens is not None
    parts, profiles = [], {}
    for m in models:
        note_if_hidden(m, conf, source="--consensus")
        p = model_profile(catalog, m)
        profiles[m] = p
        # consensus ask is single-shot only — a split lane would multiply the
        # fan-out by the model count with no corroboration payoff. Honest
        # refusal up front, before any spend.
        if int(len(prompt) * density_factor(prompt)) > p.single_shot_chars:
            usage_exit(
                f"input too large for --consensus on '{m}' (consensus asks "
                "are single-shot) — shrink the input, or drop --consensus "
                "and let the normal ask lane split it")
        parts.append(m.split('/')[-1])
    print(f"ambient: consensus ask across {len(parts)} models "
          f"({', '.join(parts)})", file=sys.stderr)
    messages = ([{"role": "system", "content": spec.system}]
                if spec.system else []) \
        + [{"role": "user", "content": prompt}]
    results = [None] * len(models)   # (content, usage, served) per model
    failures = [None] * len(models)  # ChatError per model
    gate = threading.Semaphore(_resolve_parallel(spec))
    cancel_event = threading.Event()

    def _one(idx, m):
        if cancel_event.is_set():
            raise ChatError("cancelled", "consensus ask cancelled")
        # SACRED: the --consensus set IS the user's model choice —
        # fallback must never substitute a member; a workerless model is
        # reported as its own failure instead. Each worker rides its own
        # REPLACED spec (never a mutated copy): _no_fallback pinned on, the
        # budget re-derived for THIS model unless the user set it explicitly.
        wa = dataclasses.replace(
            spec, _no_fallback=True,
            max_tokens=spec.max_tokens if explicit_max else None)
        wa = wa.with_output_budget(profiles[m], len(prompt))
        with gate:
            if cancel_event.is_set():
                raise ChatError("cancelled", "consensus ask cancelled")
            try:
                content, usage, body = complete(api_key, api_url, m,
                                                messages, wa, session=session)
            except NetworkError:
                cancel_event.set()
                raise
            except ChatError as err:
                if err.category in ("key", "funds"):
                    cancel_event.set()
                raise
        partial = (bool(body.get("salvaged_partial"))
                   or body.get("finish_reason") == "length")
        return content, usage, body.get("_served_model", m), partial

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_resolve_parallel(spec), len(models)))

    def _abort():
        cancel_event.set()
        try:
            pool.shutdown(wait=False, cancel_futures=True)  # py3.9+
        except TypeError:
            pool.shutdown(wait=False)                       # py3.8

    try:
        futs = {pool.submit(_one, i, m): i for i, m in enumerate(models)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except ChatError as err:
                if err.category in ("key", "funds"):
                    _abort()
                    raise  # every sibling is doomed identically
                failures[i] = err
            except NetworkError:
                _abort()
                raise
            except BaseException:
                # worker-side fatal → fail-fast, no
                # sibling may keep billing during the unwind.
                _abort()
                raise
    except KeyboardInterrupt:
        print("\nambient: cancelling consensus ask…", file=sys.stderr)
        _abort()
        # Match cmd_map: non-daemon pool workers are joined by
        # concurrent.futures' atexit at shutdown, so re-raising would stall
        # exit-130 for up to --timeout if a sibling is mid-call. os._exit
        # skips teardown — flush BOTH streams first so nothing is lost.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    pool.shutdown(wait=True)
    answered = [(m, r) for m, r in zip(models, results) if r is not None]
    failed = [(m, e) for m, e in zip(models, failures) if e is not None]
    level, mean, note = _answers_agreement([r[0] for _m, r in answered])
    partial = bool(failed) or any(r[3] for _m, r in answered)
    reason = (f"{len(failed)}/{len(models)} model(s) failed: "
              + ", ".join(m for m, _e in failed)) if failed else None
    if want_json:
        answers = []
        for m, r, e in zip(models, results, failures):
            if r is not None:
                answers.append({"model": m, "content": r[0],
                                "partial": r[3], "served_model": r[2]})
            else:
                answers.append({"model": m, "error": {
                    "category": e.category,
                    "diagnosis": redact(e.diagnosis, api_key)}})
        emit_json(
            "ask", model=",".join(models), api_key=api_key,
            partial=partial, reason=reason,
            allow_partial=spec.allow_partial,
            extra={"consensus": list(models), "answers": answers,
                   "agreement": {"level": level,
                                 "similarity": round(mean, 3),
                                 "note": note}})
        return
    print(f"Consensus ask across {len(models)} models:\n")
    for m, r in zip(models, results):
        print(redact(f"=== {m} ===", api_key))
        if r is None:
            continue
        content, usage, served, was_partial = r
        if was_partial:
            print(paint("⚠ PARTIAL (salvaged/truncated)", "1;33"))
        print(redact(content, api_key) + "\n")
        if usage:
            print(redact(f"[ambient {served} | in={usage.get('prompt_tokens')} "
                         f"out={usage.get('completion_tokens')} tokens"
                         f"{savings_note(served, usage, catalog, conf)}]",
                         api_key), file=sys.stderr)
    for m, e in failed:
        print(redact(f"=== {m} ===\n(failed [{e.category}]: {e.diagnosis})\n",
                     api_key))
    if answered:
        print(redact(f"Agreement: {level} — {note}", api_key))
    else:
        print("No usable answer from any model — this is NOT a clean result. "
              "Retry or check `ambient-codex models`.")
        sys.exit(EXIT_PARTIAL)
    if partial and not spec.allow_partial:
        sys.exit(EXIT_PARTIAL)
