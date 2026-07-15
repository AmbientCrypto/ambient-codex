"""Bounded repository and file audit command orchestration."""

import concurrent.futures
import dataclasses
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AuditDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


_STRUCTURED_RETRY_INSTRUCTION = """

Your previous attempt did not produce a usable structured audit. Start fresh.
Return one valid JSON object matching the requested findings schema. Do not emit
reasoning, commentary, Markdown fences, or text before or after the JSON object."""


def _structured_retry_messages(content, structured, messages, parse_audit):
    """Build one fresh structured retry without replaying untrusted output."""
    if not structured:
        return None
    parsed = parse_audit(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        return None
    if len(messages) != 2 or messages[0].get("role") != "system":
        raise ValueError("structured audit retry requires system and user messages")
    return [
        {**messages[0],
         "content": messages[0]["content"] + _STRUCTURED_RETRY_INSTRUCTION},
        dict(messages[1]),
    ]


def run_audit(args, api_key, api_url, conf, deps):
    AUDIT_FINDINGS_SCHEMA = deps.AUDIT_FINDINGS_SCHEMA
    AUDIT_JSON_INSTRUCTION = deps.AUDIT_JSON_INSTRUCTION
    AUDIT_SYNTH_PROMPT = deps.AUDIT_SYNTH_PROMPT
    AUDIT_SYSTEM_PROMPT = deps.AUDIT_SYSTEM_PROMPT
    ChatError = deps.ChatError
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    EXIT_USAGE = deps.EXIT_USAGE
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    SEVERITY_ORDER = deps.SEVERITY_ORDER
    Session = deps.Session
    _audit_split_estimate = deps._audit_split_estimate
    _best_of_audit_misses = deps._best_of_audit_misses
    _consensus_estimate = deps._consensus_estimate
    _fail = deps._fail
    _fail_exit = deps._fail_exit
    _finding_sig = deps._finding_sig
    _parse_consensus_models = deps._parse_consensus_models
    _print_repo_plan = deps._print_repo_plan
    _resolve_best_of = deps._resolve_best_of
    _resolve_parallel = deps._resolve_parallel
    _titles_match = deps._titles_match
    _verdict_from = deps._verdict_from
    adaptive_response_format = deps.adaptive_response_format
    apply_output_budget = deps.apply_output_budget
    build_code_map = deps.build_code_map
    code_map_budget = deps.code_map_budget
    complete = deps.complete
    density_factor = deps.density_factor
    emit_json = deps.emit_json
    files_block = deps.files_block
    findings_reducer = deps.findings_reducer
    git_diff_inputs = deps.git_diff_inputs
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    pack_chunks = deps.pack_chunks
    parse_audit_object = deps.parse_audit_object
    read_files = deps.read_files
    read_stdin_if_piped = deps.read_stdin_if_piped
    redact = deps.redact
    refuse_if_secrets = deps.refuse_if_secrets
    render_findings = deps.render_findings
    render_result = deps.render_result
    repo_audit_inputs = deps.repo_audit_inputs
    resolve_model = deps.resolve_model
    resolve_reduce_model = deps.resolve_reduce_model
    route_model = deps.route_model
    run_cross_file_pass = deps.run_cross_file_pass
    run_map_reduce = deps.run_map_reduce
    run_one_audit = deps.run_one_audit
    usage_exit = deps.usage_exit
    warn_if_stdin_ignored = deps.warn_if_stdin_ignored
    with_line_gutters = deps.with_line_gutters
    labeled = []
    repo_meta = None
    if getattr(args, "repo", None) is not None:
        if getattr(args, "staged", False) \
                or getattr(args, "diff", None) is not None or args.paths:
            _fail_exit(args, "audit", "usage",
                       "--repo cannot be combined with file paths or "
                       "--staged/--diff — it enumerates the repository "
                       "itself (narrow with --repo DIR/sub and --focus).",
                       exit_code=EXIT_USAGE, api_key=api_key)
        labeled, repo_meta = repo_audit_inputs(args, api_key)
        warn_if_stdin_ignored("--repo builds its own inputs from the repository")
    elif getattr(args, "staged", False) or getattr(args, "diff", None) is not None:
        labeled = git_diff_inputs(args.staged, getattr(args, "diff", None) or "HEAD")
        warn_if_stdin_ignored("--staged/--diff builds its own inputs from git")
    elif args.paths:
        # Gutter file inputs with absolute line numbers for exact citations.
        labeled.extend(with_line_gutters(read_files(args.paths)))
        warn_if_stdin_ignored("stdin is only read when no file paths are passed")
    else:
        # stdin only when no paths given (see cmd_ask: avoids blocking on
        # wrapper-held stdin). A diff is NOT guttered — its @@ hunks carry the
        # real new-file line numbers, which we tell the model to cite.
        piped = read_stdin_if_piped().strip()
        if piped:
            labeled.append(("DIFF / CODE (stdin)", piped))
    if args.focus:
        labeled.append(("focus", args.focus))
    refuse_if_secrets(labeled, args.allow_secrets)
    if args.focus:
        labeled.pop()
    if not labeled:
        _fail_exit(
            args, "audit", "usage",
            "nothing to audit. Pass file paths and/or pipe a diff:\n"
            "  git diff | ambient-codex audit\n  ambient-codex audit src/foo.py src/bar.py",
            exit_code=EXIT_USAGE, api_key=api_key,
        )
    focus = f"\nAudit focus: {args.focus}" if args.focus else ""
    # 7a: K independent samples of the SAME audit (validated + temperature-
    # bumped before any spend). Mutually exclusive with --consensus: they are
    # two different corroboration lanes.
    best_of_k = _resolve_best_of(args)
    if best_of_k and getattr(args, "consensus", None):
        usage_exit("--best-of cannot be combined with --consensus — pick one "
                   "corroboration lane")
    if getattr(args, "model", None):
        note_if_hidden(args.model, conf)
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    catalog = session.catalog()  # memoized: ONE fetch for the whole command
    total = sum(len(text) for _, text in labeled)
    # Token-dense input (CJK etc.) holds more tokens per char than the 3.2
    # assumption — inflate the effective size so sizing decisions stay safe.
    dens = density_factor("".join(t for _, t in labeled[:8])[:200_000])
    eff_total = int(total * dens)
    if getattr(args, "consensus", None):
        # Consensus names its models explicitly — the default-lane resolution
        # below is only bookkeeping; don't auto-route or hint on its behalf.
        model = resolve_model(args, conf, "chat")
    else:
        model = route_model(args, conf, "chat", catalog,
                            input_chars=eff_total)
    reduce_model = resolve_reduce_model(args, conf, model, catalog=catalog)
    profile = model_profile(catalog, model)
    single, chunk = profile.single_shot_chars, profile.chunk_chars
    # Remember whether --max-tokens came from the USER before the next line
    # resolves it for the DEFAULT model — consensus workers must not mistake
    # that derived number for an explicit choice and inherit it (R4).
    explicit_max_tokens = getattr(args, "max_tokens", None) is not None
    # A5: capture the user's RAW --max-tokens BEFORE apply_output_budget clamps
    # args.max_tokens to the DEFAULT-lane model's profile. Reading it back AFTER
    # the clamp (the old `explicit_mt = args.max_tokens`) handed every
    # consensus/best-of worker the default model's clamped number instead of the
    # user's value — and under --consensus the clamping model may not even be a
    # member of the set. Each worker must re-derive against its OWN profile.
    original_max_tokens = getattr(args, "max_tokens", None)
    # Right-size the output budget to the actual work (A1): whole input if it
    # fits single-shot, else a chunk's worth.
    apply_output_budget(args, profile, total if eff_total <= single else chunk)
    # what the consensus/best-of worker specs will carry as max_tokens (the
    # user's explicit RAW budget, or None = per-model auto) — threaded into the
    # shared estimates so plan == gate == live.
    explicit_mt = original_max_tokens if explicit_max_tokens else None

    consensus_models = None
    if getattr(args, "consensus", None):
        # Parse + validate the consensus set BEFORE the --repo plan / dry-run
        # plan prints (and before anything is billed): the plan must price the
        # real, valid model set — never the lone default model, and never a set
        # the gate would then refuse as unknown. Applies to dry-run
        # too, so `--consensus bad --dry-run` errors instead of a fake plan.
        consensus_models = _parse_consensus_models(args, catalog, api_key)

    if repo_meta is not None and not getattr(args, "dry_run", False):
        # 5b: report file count / chars / est. cost BEFORE any spend.
        _print_repo_plan(repo_meta, catalog, model, reduce_model, labeled,
                         total, eff_total, profile, dens, args, api_key,
                         consensus_models=consensus_models,
                         best_of=best_of_k, explicit_mt=explicit_mt,
                         conf=conf)

    if getattr(args, "dry_run", False):
        # preview using the SAME sizing functions, then exit without a call.
        one_pass = eff_total <= single
        structured_preview = args.format in ("json", "report")
        if consensus_models:
            # Consensus dry-run: price the real model set via the SAME shared
            # helper the live gate uses (map-only, per-model chunking) — never
            # the lone default model.
            expected, bound, _parts, chunks_by, assumed = _consensus_estimate(
                catalog, consensus_models, labeled, total, explicit_mt)
            if structured_preview:
                # --json/--report must ALWAYS emit valid JSON, never prose —
                # a plan object mirroring _print_repo_plan's dry-run shape.
                plan = {"schema_version": 1, "kind": "audit", "status": "plan",
                        "dry_run": True,
                        "model": "consensus:" + ",".join(consensus_models),
                        "consensus": list(consensus_models),
                        "input_chars": total, "items": len(labeled),
                        "n_chunks": sum(chunks_by.values()),
                        "n_chunks_by_model": chunks_by}
                print(redact(json.dumps(plan, separators=(",", ":")), api_key))
                return
            print("ambient-codex audit plan (dry run — nothing sent):")
            print(f"  models:       consensus: {', '.join(consensus_models)}")
            print(f"  input:        {total:,} chars across "
                  f"{len(labeled)} labeled item(s)")
            print(f"  strategy:     {sum(chunks_by.values())} map call(s) "
                  f"across {len(consensus_models)} model(s) "
                  "(deterministic merge — no synthesis)")
            print(f"  format:       {args.format}")
            return
        # Mirror the live path's chunking (M2): the shared split helper
        # prices map/reduce lanes exactly like the live run
        # will — incl. the deterministic structured merge
        # that makes NO synthesis call.
        if best_of_k:
            # The plan prices the FULL K-sample work; the live gate then
            # prices only the cache-missing share of it — equal on a
            # cold cache, strictly less on a resume, never more.
            expected, bound, _parts, chunks_by, assumed = _consensus_estimate(
                catalog, [model] * best_of_k, labeled, total, explicit_mt,
                fb_args=args, fb_conf=conf)
            n_chunks = sum(chunks_by.values())
        else:
            n_chunks, expected, bound, assumed = _audit_split_estimate(
                catalog, model, reduce_model, labeled, total, eff_total,
                profile, dens, args.max_tokens, structured_preview,
                fb_args=args, fb_conf=conf)
        if structured_preview:
            # --json/--report must ALWAYS emit valid JSON, never prose —
            # a plan object mirroring _print_repo_plan's dry-run shape.
            plan = {"schema_version": 1, "kind": "audit", "status": "plan",
                    "dry_run": True, "model": model,
                    "input_chars": total, "items": len(labeled),
                    "one_pass": one_pass,
                    "n_chunks": 1 if one_pass else n_chunks,
                    "output_budget": args.max_tokens}
            if best_of_k:
                plan["best_of"] = best_of_k
            if not one_pass and reduce_model != model:
                plan["reduce_model"] = reduce_model
            print(redact(json.dumps(plan, separators=(",", ":")), api_key))
            return
        print("ambient-codex audit plan (dry run — nothing sent):")
        print(f"  model:        {model} ({'reasoning' if profile.is_reasoning else 'direct'})")
        if best_of_k:
            print(f"  best-of:      {best_of_k} independent samples "
                  f"(all K priced below)")
        if not one_pass and reduce_model != model and not structured_preview:
            print(f"  models:       map={model}, reduce={reduce_model}")
        print(f"  input:        {total:,} chars across {len(labeled)} labeled item(s)")
        print(f"  output budget: {args.max_tokens:,} tokens/call")
        print(f"  strategy:     {'single-shot (one pass)' if one_pass else f'map-reduce → {n_chunks} chunks, up to {min(_resolve_parallel(args), n_chunks)} parallel + synthesis'}")
        print(f"  format:       {args.format}")
        return

    if consensus_models:
        # run the same audit on several models; rank findings corroborated by
        # 2+ models first. Usually only ~2 models are READY at once — be honest.
        # (The set was parsed + validated above, BEFORE the plan printed.)
        models = consensus_models
        for m in models:
            note_if_hidden(m, conf, source="--consensus")
        # 5c under consensus: the deep cross-file confirmation pass does NOT
        # run — multi-model corroboration already cross-checks findings, and
        # a per-model deep pass would multiply the "at most ONE extra call"
        # bound by the model count. Say so instead of silently ignoring the
        # flags: --deep/--no-deep are documented no-ops here.
        if getattr(args, "repo", None) is not None \
                or getattr(args, "deep", None) is not None:
            print("ambient: note — the deep cross-file pass is skipped "
                  "under --consensus (multi-model corroboration already "
                  "cross-checks findings); --deep/--no-deep have no effect "
                  "here", file=sys.stderr)
        # The SAME shared per-model estimate the --repo plan printed
        # (_consensus_estimate), so plan and gate can never disagree
        # each model priced on its own chunking, map-only.
        _expected_sum, _bound_sum, parts, _per_chunks, _assumed = \
            _consensus_estimate(catalog, models, labeled, total, explicit_mt)
        print(f"ambient: consensus across {len(parts)} models "
              f"({', '.join(parts)})", file=sys.stderr)
        agg = []
        failed = []
        # each model derives its OWN profile budget — args.max_tokens was
        # resolved for the DEFAULT model above, and inheriting that number
        # would mis-budget every other model in the set. An explicit
        # --max-tokens from the user still applies to all of them.
        # SACRED: the --consensus set IS the user's model choice —
        # --fallback/AMBIENT_FALLBACK must never substitute a member. A
        # workerless model is reported as ITS OWN failure below. The worker
        # spec is a frozen REPLACE, never a mutated args copy.
        # A5: the RAW user --max-tokens (original_max_tokens), NOT the
        # apply_output_budget-clamped args.max_tokens — each worker re-derives
        # against its OWN profile via with_output_budget, so a larger consensus
        # member can use its bigger budget instead of the default model's clamp.
        worker_spec = dataclasses.replace(
            RequestSpec.from_args(args), _no_fallback=True,
            max_tokens=original_max_tokens if explicit_max_tokens else None)
        # ONE shared gate caps TOTAL concurrent network calls across every
        # model's inner fan-out at the resolved width — without it N models ×
        # N chunks each = width² simultaneous calls (bounded).
        gate = threading.Semaphore(_resolve_parallel(args))
        # flipped on the first fatal error / Ctrl-C so every sibling's
        # map-reduce stops STARTING chunks — billing must not continue while
        # the consensus unwinds.
        cancel_event = threading.Event()
        # Fan the per-model audits out concurrently (each run_one_audit builds
        # its own isolated Namespace and pool, so nothing is shared-mutable);
        # aggregation below still walks the CALLER's model order, so ranking
        # and corroboration stay deterministic regardless of finish order.
        results_by_model = [None] * len(models)
        worker_errs = [None] * len(models)

        def _one_consensus_audit(m):
            print(f"ambient: consensus — auditing with {m}…", file=sys.stderr)
            return run_one_audit(m, catalog, labeled,
                                 AUDIT_SYSTEM_PROMPT + focus,
                                 worker_spec, api_key, api_url, conf,
                                 gate=gate, cancel_event=cancel_event,
                                 session=session)

        cpool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(_resolve_parallel(args), len(models)))

        def _abort_consensus():
            cancel_event.set()
            try:
                cpool.shutdown(wait=False, cancel_futures=True)  # py3.9+
            except TypeError:
                cpool.shutdown(wait=False)                       # py3.8

        # The executor is managed EXPLICITLY: a `with` block would re-raise
        # only after ALL futures finish, so a fatal error or Ctrl-C would sit
        # waiting on every in-flight model while billing continued.
        try:
            futs = {cpool.submit(_one_consensus_audit, m): i
                    for i, m in enumerate(models)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    results_by_model[i] = fut.result()
                except ChatError as err:
                    if err.category in ("key", "funds"):
                        # Every sibling is doomed identically — cancel queued
                        # models, stop in-flight chunk starts, and surface the
                        # real problem NOW (the old sequential fail-fast).
                        _abort_consensus()
                        raise
                    worker_errs[i] = err
                except NetworkError:
                    # The API is unreachable for everyone — abort like the old
                    # sequential lane instead of draining N doomed models.
                    _abort_consensus()
                    raise
                except Exception as err:  # noqa: BLE001 — re-raised below in
                    # model order, preserving the sequential lane's semantics.
                    worker_errs[i] = err
                except BaseException:
                    # worker-side fatal → fail-fast,
                    # no sibling model may keep billing during the unwind.
                    _abort_consensus()
                    raise
        except KeyboardInterrupt:
            print("\nambient: cancelling consensus…", file=sys.stderr)
            _abort_consensus()
            # Match cmd_map: non-daemon pool workers are joined by
            # concurrent.futures' atexit at shutdown, so re-raising would stall
            # exit-130 for up to --timeout if a sibling is mid-call. os._exit
            # skips teardown — flush BOTH streams first so nothing is lost.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(130)
        cpool.shutdown(wait=True)
        first_err = next((e for e in worker_errs if e is not None), None)
        if first_err is not None:
            raise first_err
        for m, (findings, ok) in zip(models, results_by_model):
            if not ok:
                failed.append(m)
            for f in findings:
                if not isinstance(f, dict):
                    continue
                mm = re.match(r"^(.*?):(\d+)$", str(f.get("file", "")))
                if mm:
                    f["file"], f["line"] = mm.group(1), int(mm.group(2))
                path, line, title = _finding_sig(f)
                slot = None
                for s in agg:
                    sp, sl, st = s["sig"]
                    if sp == path and _titles_match(st, title) \
                            and abs(sl - line) <= 3:
                        slot = s
                        break
                if slot is None:
                    agg.append({"sig": (path, line, title), "f": f, "models": {m}})
                    continue
                slot["models"].add(m)
                if SEVERITY_ORDER.get(f.get("severity"), 9) < \
                        SEVERITY_ORDER.get(slot["f"].get("severity"), 9):
                    slot["f"] = f
        ranked = sorted(agg,
                        key=lambda s: (-len(s["models"]),
                                       SEVERITY_ORDER.get(s["f"].get("severity"), 9)))
        partial = bool(failed)
        if args.format == "json":
            # The agentic contract holds on consensus too (
            # --consensus --json used to print prose and exit 0 on 1-of-2).
            out_findings = [
                {**s["f"], "corroboration": {"models": sorted(s["models"]),
                                             "count": len(s["models"])}}
                for s in ranked
            ]
            emit_json(
                "consensus", model=",".join(models), api_key=api_key,
                findings=out_findings,
                verdict=_verdict_from([s["f"] for s in ranked], partial),
                partial=partial,
                reason=(f"{len(failed)}/{len(models)} model(s) returned no "
                        f"usable audit: {', '.join(failed)}" if failed else None),
                extra={"failed_models": failed},
                allow_partial=getattr(args, "allow_partial", False))
            return
        print(f"Consensus audit across {len(models)} models "
              f"({', '.join(models)}) — corroborated findings first:\n")
        if failed:
            print(f"⚠ {len(failed)}/{len(models)} model(s) did NOT return a usable "
                  f"audit ({', '.join(failed)}) — coverage is INCOMPLETE, not a "
                  "clean pass.\n", file=sys.stderr)
        if not ranked:
            if len(failed) == len(models):
                print("No usable audit from any model (all failed or weren't "
                      "serving) — this "
                      "is NOT a clean result. Retry or check `ambient-codex models`.")
                sys.exit(EXIT_PARTIAL)
            print("No defects found by the models that responded.")
        for s in ranked:
            f = s["f"]
            tag = f"[{len(s['models'])}/{len(models)} models]"
            print(redact(f"{tag} [{f.get('severity', '?')}] "
                         f"{f.get('file', '?')}:{f.get('line', '?')} — "
                         f"{f.get('title', '')}", api_key))
            if f.get("scenario"):
                print(redact(f"    {f['scenario']}", api_key))
        if partial and not getattr(args, "allow_partial", False):
            # Some models failed: a 1-of-2 audit is NOT a clean pass
            # — exit like every other partial surface.
            sys.exit(EXIT_PARTIAL)
        return

    if best_of_k:
        # 7a: K INDEPENDENT samples of the same audit on the SAME model,
        # findings ranked by CORROBORATION (seen in more samples first) with
        # the vote count reported. ONE up-front gate covers all K runs (the
        # same shared estimate the dry-run plan printed); each sample runs
        # through run_one_audit in its own salted cache lane so a re-run
        # resumes per sample. Fail-fast mirrors the consensus lane.
        if getattr(args, "repo", None) is not None \
                or getattr(args, "deep", None) is not None:
            # Same honesty note as --consensus: corroboration replaces the
            # deep cross-file pass — say so instead of silently ignoring it.
            print("ambient: note — the deep cross-file pass is skipped "
                  "under --best-of (sample corroboration already "
                  "cross-checks findings); --deep/--no-deep have no effect "
                  "here", file=sys.stderr)
        # H3: resolve the salted cache BEFORE the gate — a resumed best-of
        # run must gate (and bill) ONLY the samples/chunks the cache does
        # not already hold, mirroring run_map_reduce's own resume lane. On
        # a cold cache this prices the same K-sample work the dry-run plan
        # showed; on a warm one it prices strictly less — never more.
        miss_plans = _best_of_audit_misses(
            catalog, model, labeled, AUDIT_SYSTEM_PROMPT + focus, args,
            best_of_k, explicit_max_tokens, original_max_tokens)
        miss_calls_total = sum(c for c, _, _, _ in miss_plans)
        cached_samples = sum(1 for c, _, _, _ in miss_plans if c == 0)
        if miss_calls_total:
            print(f"ambient: best-of {best_of_k} audit on {model} at "
                  f"temperature {args.temperature}"
                  + (f" — {cached_samples}/{best_of_k} sample(s) fully "
                     "cached, not re-billed" if cached_samples else ""),
                  file=sys.stderr)
        else:
            print(f"ambient: best-of {best_of_k} audit — every sample is "
                  "cached (nothing to gate or re-bill)", file=sys.stderr)
        gate = threading.Semaphore(_resolve_parallel(args))
        cancel_event = threading.Event()
        results = [None] * best_of_k
        worker_errs = [None] * best_of_k

        def _one_sample(i):
            print(f"ambient: best-of — audit sample {i + 1}/{best_of_k}…",
                  file=sys.stderr)
            # per-sample cache lane (resume) via frozen replace;
            # run_one_audit re-derives the budget per profile when auto.
            # gate_fallback=False: FAN-OUT worker — the batch gate above
            # reserved any --fallback swap exposure up front.
            # A5: RAW --max-tokens (original_max_tokens), not the clamped
            # args.max_tokens — each sample re-derives against the model profile.
            sa = dataclasses.replace(
                RequestSpec.from_args(args), _cache_salt=f"best-of:{i}",
                gate_fallback=False,
                max_tokens=original_max_tokens if explicit_max_tokens else None)
            return run_one_audit(model, catalog, labeled,
                                 AUDIT_SYSTEM_PROMPT + focus, sa, api_key,
                                 api_url, conf, gate=gate,
                                 cancel_event=cancel_event, session=session)

        bpool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(_resolve_parallel(args), best_of_k))

        def _abort_best_of():
            cancel_event.set()
            try:
                bpool.shutdown(wait=False, cancel_futures=True)  # py3.9+
            except TypeError:
                bpool.shutdown(wait=False)                       # py3.8

        try:
            futs = {bpool.submit(_one_sample, i): i for i in range(best_of_k)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except ChatError as err:
                    if err.category in ("key", "funds"):
                        _abort_best_of()
                        raise  # every sibling is doomed identically
                    worker_errs[i] = err
                except NetworkError:
                    _abort_best_of()
                    raise
                except Exception as err:  # noqa: BLE001 — re-raised below
                    worker_errs[i] = err
                except BaseException:
                    # worker-side fatal → fail-fast,
                    # no sibling sample may keep billing during the unwind.
                    _abort_best_of()
                    raise
        except KeyboardInterrupt:
            print("\nambient: cancelling best-of…", file=sys.stderr)
            _abort_best_of()
            # Match cmd_map: non-daemon pool workers are joined by
            # concurrent.futures' atexit at shutdown, so re-raising would stall
            # exit-130 for up to --timeout if a sibling is mid-call. os._exit
            # skips teardown — flush BOTH streams first so nothing is lost.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(130)
        bpool.shutdown(wait=True)
        first_err = next((e for e in worker_errs if e is not None), None)
        if first_err is not None:
            raise first_err
        agg = []
        failed_n = 0
        for i, (findings, ok_sample) in enumerate(results):
            if not ok_sample:
                failed_n += 1
            for f in findings:
                if not isinstance(f, dict):
                    continue
                fm = re.match(r"^(.*?):(\d+)$", str(f.get("file", "")))
                if fm:
                    f["file"], f["line"] = fm.group(1), int(fm.group(2))
                path, line, title = _finding_sig(f)
                slot = None
                for s in agg:
                    sp, sl, st = s["sig"]
                    if sp == path and _titles_match(st, title) \
                            and abs(sl - line) <= 3:
                        slot = s
                        break
                if slot is None:
                    agg.append({"sig": (path, line, title), "f": f,
                                "votes": {i}})
                    continue
                slot["votes"].add(i)
                if SEVERITY_ORDER.get(f.get("severity"), 9) < \
                        SEVERITY_ORDER.get(slot["f"].get("severity"), 9):
                    slot["f"] = f
        ranked = sorted(
            agg, key=lambda s: (-len(s["votes"]),
                                SEVERITY_ORDER.get(s["f"].get("severity"), 9)))
        partial = failed_n > 0
        reason = (f"{failed_n}/{best_of_k} sample(s) returned no usable "
                  "audit" if failed_n else None)
        if args.format == "json":
            out_findings = [
                {**s["f"], "corroboration": {"count": len(s["votes"]),
                                             "of": best_of_k}}
                for s in ranked]
            emit_json(
                "audit", model=model, api_key=api_key,
                findings=out_findings,
                verdict=_verdict_from([s["f"] for s in ranked], partial),
                partial=partial, reason=reason,
                extra={
                    "best_of": best_of_k,
                    # SAME failed_samples shape as ask/code --best-of
                    # a list of {index, category, diagnosis},
                    # plus the additive count the old int field carried.
                    "failed_samples": [
                        {"index": i, "category": "unusable",
                         "diagnosis": "sample returned no usable audit "
                                      "(failed, unparseable, or truncated)"}
                        for i, (_f, ok_s) in enumerate(results)
                        if not ok_s],
                    "failed_sample_count": failed_n,
                },
                allow_partial=getattr(args, "allow_partial", False))
            return
        print(f"Best-of-{best_of_k} audit on {model} — corroborated "
              "findings first:\n")
        if failed_n:
            print(f"⚠ {failed_n}/{best_of_k} sample(s) did NOT return a "
                  "usable audit — coverage is INCOMPLETE, not a clean pass.\n",
                  file=sys.stderr)
        if not ranked:
            if failed_n == best_of_k:
                print("No usable audit from any sample (all failed) — this "
                      "is NOT a clean result. Retry or check `ambient "
                      "models`.")
                sys.exit(EXIT_PARTIAL)
            print("No defects found by the samples that responded.")
        for s in ranked:
            f = s["f"]
            tag = f"[{len(s['votes'])}/{best_of_k} samples]"
            print(redact(f"{tag} [{f.get('severity', '?')}] "
                         f"{f.get('file', '?')}:{f.get('line', '?')} — "
                         f"{f.get('title', '')}", api_key))
            if f.get("scenario"):
                print(redact(f"    {f['scenario']}", api_key))
        print(redact(
            f"Verdict: {_verdict_from([s['f'] for s in ranked], partial)}",
            api_key))
        if partial and not getattr(args, "allow_partial", False):
            sys.exit(EXIT_PARTIAL)
        return

    # A4: structured findings output (--json/--format report). Gate the request
    # on the model's real capability; append the shape when it lacks strict schema.
    structured = args.format in ("json", "report")
    sys_prompt = AUDIT_SYSTEM_PROMPT + focus
    reducer = None
    if structured:
        rf = adaptive_response_format(model, profile, AUDIT_FINDINGS_SCHEMA)
        args.response_format = rf
        if rf is None or rf.get("type") == "json_object":
            sys_prompt += AUDIT_JSON_INSTRUCTION
        reducer = findings_reducer

    def emit_single(content, body):
        truncated = (bool(body.get("salvaged_partial"))
                     or body.get("finish_reason") == "length")
        if structured:
            eff = render_findings(
                content, args.format, api_key, truncated,
                "output salvaged/truncated (hit token cap)" if truncated else "",
                model=model)
            if eff and not getattr(args, "allow_partial", False):
                sys.exit(EXIT_PARTIAL)
        else:
            render_result(content, truncated,
                          "output was salvaged/truncated", args, api_key,
                          None, model)

    if eff_total <= single:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": files_block(labeled)},
        ]
        try:
            content, _usage, body = complete(api_key, api_url, model, messages,
                                             args, session=session)
            retry_messages = _structured_retry_messages(
                content, structured, messages, parse_audit_object)
            if retry_messages is not None:
                print("ambient: structured audit response was unusable — "
                      "retrying once with a fresh JSON-only request",
                      file=sys.stderr)
                content, _usage, body = complete(
                    api_key, api_url, model, retry_messages, args,
                    session=session)
            emit_single(content, body)
            return
        except ChatError as err:
            if err.category not in ("empty", "context"):
                _fail(args, "audit", err, api_key)
            print(
                f"ambient: '{model}' couldn't finish the whole input in one pass "
                "— splitting into smaller pieces for the same model",
                file=sys.stderr,
            )
    # Recovery chunk size: never exceed the single-shot budget (a 20k floor
    # would top a 16k-output reasoner's ~17.6k single), and shrink
    # for token-dense input so chunks can't 400 on context.
    chunk_chars = min(chunk if eff_total > single else max(total // 3 + 1000, 20_000),
                      single)
    chunk_chars = max(MIN_REASONING_CHUNK, int(chunk_chars / dens))
    packed = pack_chunks(labeled, chunk_chars)
    # The repo map is injected into EVERY chunk (real billed input) — gate
    # on total + map x chunk-count so the gate matches the actual spend
    #. A deterministic reducer (structured findings merge) makes
    # NO synthesis LLM call — price only the map lane.
    code_map = build_code_map(labeled, budget=code_map_budget(single))
    final, partial, reason = run_map_reduce(
        api_key, api_url, model, sys_prompt,
        packed, args, AUDIT_SYNTH_PROMPT, single, reducer=reducer,
        code_map=code_map,
        reduce_model=reduce_model, catalog=catalog, session=session,
    )
    # 5c: bounded cross-file confirmation — opt-out via --no-deep; ON by
    # default only for --repo (a chunked whole-repo pass is exactly where
    # cross-file claims go unverified). At most ONE extra gated call.
    deep = getattr(args, "deep", None)
    if deep is None:
        deep = getattr(args, "repo", None) is not None
    if deep and len(packed) > 1:
        final = run_cross_file_pass(final, labeled, model, profile, args,
                                    api_key, api_url, catalog, conf,
                                    structured, session=session)
    # Repo files EXCLUDED by the input ceiling make coverage PARTIAL as a FACT —
    # never rely on the model reading the coverage note and self-declaring it. A
    # clean SHIP must not report coverage_complete over skipped files, so force
    # the partial flag deterministically (render_findings then flips SHIP→NEEDS
    # WORK and coverage_complete→false; exit stays 2 unless --allow-partial).
    omitted = repo_meta.get("omitted_over_cap", 0) if repo_meta else 0
    omitted_oversize = repo_meta.get("omitted_oversize", 0) \
        if repo_meta else 0
    if omitted or omitted_oversize:
        partial = True
        reason = ((reason + "; ") if reason else "") + \
            "; ".join(filter(None, (
                f"{omitted} repo file(s) excluded by the input ceiling"
                if omitted else "",
                f"{omitted_oversize} source file(s) excluded by the per-file "
                "ceiling" if omitted_oversize else "",
            )))
    if structured:
        eff = render_findings(final, args.format, api_key, partial, reason,
                              model=model)
        if eff and not getattr(args, "allow_partial", False):
            sys.exit(EXIT_PARTIAL)
    else:
        render_result(final, partial, reason, args, api_key)



__all__ = ("AuditDependencies", "run_audit")
