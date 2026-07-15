"""Model-aware, resumable build command orchestration."""

import argparse
import hashlib
import os
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class BuildDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_build(args, api_key, api_url, conf, deps):
    """Native agentic build: plan → generate → emit a FILE
    MANIFEST. Writes happen ONLY inside --dir, only with --apply (+ --yes when
    headless), through the safe_relpath firewall. NOTHING is ever executed —
    advisory_steps are printed as text. Truncation requeues files in smaller
    batches; state in .ambient-build.json makes any interrupted run resumable
    without re-billing finished files."""
    BUILD_GEN_PROMPT = deps.BUILD_GEN_PROMPT
    BUILD_JSON_INSTRUCTION_PLAN = deps.BUILD_JSON_INSTRUCTION_PLAN
    BUILD_PLAN_PROMPT = deps.BUILD_PLAN_PROMPT
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    ChatError = deps.ChatError
    DEFAULT_CODE_MODEL = deps.DEFAULT_CODE_MODEL
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    MAX_BUDGET_ESCALATIONS = deps.MAX_BUDGET_ESCALATIONS
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    Session = deps.Session
    _build_apply = deps._build_apply
    _build_workflow = deps._build_workflow
    _context_safe_escalation_ceiling = deps._context_safe_escalation_ceiling
    _effective_cpt = deps._effective_cpt
    _fail = deps._fail
    _fail_exit = deps._fail_exit
    _json_mode = deps._json_mode
    _load_build_state = deps._load_build_state
    _parse_file_records = deps._parse_file_records
    _save_build_state = deps._save_build_state
    _served_model_of = deps._served_model_of
    _stdin_is_tty = deps._stdin_is_tty
    _within_root = deps._within_root
    apply_output_budget = deps.apply_output_budget
    build_plan_rf_ladder = deps.build_plan_rf_ladder
    build_resume_identity = deps.build_resume_identity
    cap_state = deps.cap_state
    complete = deps.complete
    density_factor = deps.density_factor
    emit_json = deps.emit_json
    emit_json_error = deps.emit_json_error
    estimate_cost_fb = deps.estimate_cost_fb
    estimate_cost_mr_fb = deps.estimate_cost_mr_fb
    extract_json = deps.extract_json
    files_block = deps.files_block
    is_auto_model = deps.is_auto_model
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    pack_chunks = deps.pack_chunks
    progress_display_enabled = deps.progress_display_enabled
    read_files = deps.read_files
    record_cap = deps.record_cap
    redact = deps.redact
    refuse_if_secrets = deps.refuse_if_secrets
    resolve_model = deps.resolve_model
    resolve_reduce_model = deps.resolve_reduce_model
    route_model = deps.route_model
    run_map_reduce = deps.run_map_reduce
    safe_relpath = deps.safe_relpath
    usage_exit = deps.usage_exit
    warn_if_stdin_ignored = deps.warn_if_stdin_ignored
    task = " ".join(args.task).strip()
    dry_run_auto_model = None
    if not task:
        usage_exit('describe what to build: '
                   'ambient-codex build "a flask todo API with tests" --dir out')
    # Validate the caps UP FRONT (before resume/catalog/spend). A non-positive
    # --max-files becomes a negative Python slice (obj["plan"][:args.max_files])
    # that keeps all-but-the-last file — an invalid "cap" that still proceeds.
    if args.max_files < 1:
        usage_exit("--max-files must be a positive integer")
    if args.max_file_bytes < 1:
        usage_exit("--max-file-bytes must be a positive integer")
    warn_if_stdin_ignored("`build` does not read stdin — pass context with -f FILE")
    refuse_if_secrets([("task", task)], args.allow_secrets)
    root = os.path.abspath(os.path.expanduser(args.dir or "."))
    headless = not _stdin_is_tty()   # M24: a closed stdin must not crash isatty()
    if args.apply and headless:
        if not args.dir:
            usage_exit("headless --apply requires an explicit --dir "
                       "(refusing to write into an implicit working directory)")
        if not args.yes:
            usage_exit("headless --apply also requires --yes "
                       "(explicit consent to write files)")
    if getattr(args, "model", None):
        note_if_hidden(args.model, conf)
    session = Session(api_url=api_url, api_key=api_key, conf=conf)
    # DRY RUN must egress NOTHING — skip the live /v1/models fetch that the
    # non-dry-run path memoizes here. route_model / model_profile tolerate an
    # empty catalog (they fall back to the requested model spec + assumed
    # sizing), so the preview stays fully functional with zero network egress.
    # (Previously the fetch ran before the dry-run guard, sending a probe +
    # Authorization header despite the "nothing sent" contract.)
    catalog = [] if getattr(args, "dry_run", False) else session.catalog()
    ctx_probe = 0  # cheap size probe (stat only) for routing
    for p in (args.context or []):
        try:
            ctx_probe += os.path.getsize(p)
        except OSError:
            pass
    if getattr(args, "dry_run", False):
        # No catalog was fetched (zero egress), so an `auto` spec cannot resolve
        # to a concrete READY pick — resolve_auto_model would exit "no model is
        # serving". Name the concrete -m model, or the lane default for `auto`,
        # so the plan (and model_profile sizing below) has a real model.
        model = resolve_model(args, conf, "code")
        if is_auto_model(model):
            dry_run_auto_model = model
            model = DEFAULT_CODE_MODEL
    else:
        model = route_model(args, conf, "code", catalog,
                            input_chars=len(task) + ctx_probe)
    reduce_model = resolve_reduce_model(args, conf, model, catalog=catalog)
    profile = model_profile(catalog, model)
    single = profile.single_shot_chars
    # Generation wants the full budget: reasoning + several complete files.
    apply_output_budget(args, profile, single)

    context = ""
    # Digest of the RAW (pre-distillation) context so that editing any -f file
    # invalidates the resume cache (task_sha) instead of serving a stale plan.
    raw_context_sha = ""
    needs_distill = False
    packed = None
    if args.context:
        chunks = read_files(args.context)
        refuse_if_secrets(chunks, args.allow_secrets)
        context = files_block(chunks)
        raw_context_sha = hashlib.sha256(context.encode()).hexdigest()
        needs_distill = len(context) > single // 2
        if needs_distill:
            # Offline (no egress) — computed once so the dry-run estimate below
            # and the live distillation both use the REAL packed-chunk count.
            packed = pack_chunks(chunks,
                                 max(MIN_REASONING_CHUNK,
                                     int(profile.chunk_chars
                                         / density_factor(context))))

    # DRY RUN spends nothing and egresses nothing — decide + return BEFORE the
    # context-distillation map-reduce, which makes billed calls and sends file
    # contents to the network. A flag that says "nothing sent" must send nothing.
    if getattr(args, "dry_run", False):
        gen_calls = max(2, args.max_files // 4 + 2)
        # Each live generation call re-sends the FULL prompt: task + context +
        # plan_overview + a ~2000-char scaffold (see the live gate below). The
        # dry-run can't know plan_overview (it's model-generated at plan time),
        # but it MUST include the context and the fixed overhead, or the preview
        # under-prices what the live gate will actually reserve. In the distill
        # case the generation calls send the DISTILLED context, which the live
        # lane caps at ~single//2, so estimate with that ceiling.
        GEN_OVERHEAD = 2000
        if needs_distill:
            # The distillation lane is priced by the SAME split helper the
            # live distillation run below uses (M2) — same chunk count, same +1
            # generation call. The remaining generation calls each re-send the
            # distilled context (capped at ~single//2) + task + overhead.
            gen_ctx = min(len(context), single // 2)
            gen_call_chars = len(task) + gen_ctx + GEN_OVERHEAD
            expected, bound, assumed = estimate_cost_mr_fb(
                catalog, model, reduce_model, len(context), len(packed),
                args.max_tokens, args, conf, extra_calls=1,
                per_call_chars=[len(c) for c in packed])
            e_g, b_g, a_g = estimate_cost_fb(
                catalog, model, gen_call_chars, gen_calls - 1, args.max_tokens,
                args, conf, per_call_chars=[gen_call_chars] * (gen_calls - 1))
            expected, bound = expected + e_g, bound + b_g
            assumed = assumed or a_g
        else:
            gen_call_chars = len(task) + len(context) + GEN_OVERHEAD
            expected, bound, assumed = estimate_cost_fb(
                catalog, model, gen_call_chars, gen_calls,
                args.max_tokens, args, conf,
                per_call_chars=[gen_call_chars] * gen_calls)
        print("ambient-codex build plan (dry run — nothing sent):")
        if dry_run_auto_model:
            print(f"  model:         {dry_run_auto_model} (live selection deferred; "
                  f"sizing preview uses {model})")
        else:
            print(f"  model:         {model} "
                  f"({'reasoning' if profile.is_reasoning else 'direct'})")
        if needs_distill and reduce_model != model:
            print(f"  models:        map={model}, reduce={reduce_model}")
        print(f"  task:          {len(task):,} chars"
              + (f" + context {len(context):,} chars" if context else ""))
        if needs_distill:
            print("  note:          context exceeds half the window — a live "
                  "run distills it first (extra billed calls)")
        print(f"  output budget: {args.max_tokens:,} tokens/call")
        print(f"  target dir:    {root} "
              f"({'writes ON (--apply)' if args.apply else 'manifest only'})")
        print(f"  caps:          ≤{args.max_files} files, "
              f"≤{args.max_file_bytes:,} bytes/file")
        return

    # LIVE path only: distill an oversized context now that dry-run is ruled out.
    if needs_distill:
        print("ambient: context exceeds half this model's window — "
              "distilling it first", file=sys.stderr)
        context, c_partial, c_reason = run_map_reduce(
            api_key, api_url, model,
            f"TASK: {task}\nFrom the given chunk of context files, extract "
            "verbatim every part (signatures, types, conventions, key "
            "logic) needed to build the task. Be selective.",
            packed, args,
            f"TASK: {task}\nMerge these context extracts into one concise "
            "brief preserving verbatim signatures/types.",
            single, reduce_model=reduce_model, catalog=catalog,
            session=session,
        )
        if c_partial and not getattr(args, "allow_partial", False):
            _fail_exit(args, "build", "partial",
                       f"context distillation was incomplete ({c_reason}) — "
                       "re-run, narrow -f, or pass --allow-partial.",
                       api_key=api_key)
        context = context[:max(1000, single // 2)]

    # Resume only when every generation-affecting input matches. In particular,
    # a larger token budget or changed temperature must not reuse files produced
    # under the previous request shape.
    task_sha = build_resume_identity(
        task=task,
        model=model,
        reduce_model=reduce_model,
        context_paths=args.context,
        raw_context_sha=raw_context_sha,
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    # max_plan = the user's OWN --max-files: task_sha matching is cache identity,
    # not integrity, so a poisoned same-hash state must not exceed the cap.
    state = None if args.no_resume else _load_build_state(
        root, task_sha, max_plan=args.max_files,
        max_file_bytes=args.max_file_bytes)
    if state:
        print(f"ambient: previous run found for this exact task — resuming "
              f"({len(state['done'])} file(s) already generated; "
              "--no-resume starts fresh)", file=sys.stderr)

    def _ns_with(rf, system_extra=""):
        a = argparse.Namespace(**vars(args))
        a.response_format = rf
        return a, system_extra

    # ---- PHASE 1: PLAN (one cheap call) ------------------------------------
    if state is None:
        # A model that has repeatedly failed to produce a usable build plan
        # gets an honest heads-up up front — never a silent model swap (choice
        # is sacred). We name the reliable model and keep going on theirs.
        if cap_state(model, "build_plan") == "unreliable":
            print(f"ambient: note — {model} has struggled to produce a usable "
                  f"build plan before; {DEFAULT_CODE_MODEL} is reliable for "
                  f"builds (re-run with -m {DEFAULT_CODE_MODEL} to switch). "
                  "Trying your model anyway.", file=sys.stderr)
        user = f"TASK: {task}"
        if context:
            user += f"\n\nContext files:\n\n{context}"
        obj, served_model = None, model
        # Adaptive ladder: each attempt DOWNGRADES the structured-output demand
        # (json_schema -> json_object -> prompt-only) rather than re-sending the
        # same doomed request the model already ignored.
        attempts = build_plan_rf_ladder(model, profile)
        for attempt, attempt_rf in enumerate(attempts, 1):
            pa, extra = _ns_with(
                attempt_rf, "" if (attempt_rf and attempt_rf.get("type") == "json_schema")
                else BUILD_JSON_INSTRUCTION_PLAN)
            try:
                content, _u, _b = complete(
                    api_key, api_url, model,
                    [{"role": "system", "content": BUILD_PLAN_PROMPT + extra},
                     {"role": "user", "content": user}], pa, session=session)
            except ChatError as err:
                # Only a structured-output REJECTION (a generic 400 → 'unknown')
                # is worth downgrading for. Every other category — rate, model,
                # context, budget, key, funds, network, service — won't improve
                # on a looser rung, so abort now and surface the real cause
                # instead of a misleading 'no usable plan' (Codex round 2). We do
                # NOT record a capability outcome here: an infra failure (rate/
                # outage/funds/network) is NOT the model failing to plan, and
                # recording False would wrongly mark it unreliable (Codex round 3).
                if err.category != "unknown" or attempt == len(attempts):
                    _fail(args, "build",
                          ChatError(err.category,
                                    f"planning failed — {err.diagnosis}"),
                          api_key)
                continue
            # Track the model that ACTUALLY served on EVERY successful call (even
            # a non-JSON one) so a --fallback served model gets the credit/blame,
            # not the requested model — on the failure path too (Codex round 2).
            served_model = _served_model_of(_b, model)
            finish_reason = (str(_b.get("finish_reason") or "").lower()
                             if isinstance(_b, dict) else "")
            dirty = (
                isinstance(_b, dict)
                and (_b.get("reasoning_draft") or _b.get("salvaged_partial"))
            ) or finish_reason not in (
                "",
                "stop",
                "end_turn",
                "stop_sequence",
                "eos",
                "eos_token",
                "complete",
                "completed",
            )
            if dirty:
                obj = None
            else:
                obj = extract_json(content)
                if obj and obj.get("_repaired"):
                    obj = None
            if obj and isinstance(obj.get("plan"), list) and obj["plan"]:
                break
            obj = None
            if attempt < len(attempts):
                print("ambient: plan reply wasn't valid JSON — retrying with a "
                      "looser output format", file=sys.stderr)
                user += ("\n\nREMINDER: return ONLY the JSON object, no prose, "
                         "no fences.")
        if not (obj and isinstance(obj.get("plan"), list) and obj["plan"]):
            record_cap(served_model, "build_plan", False)
            pmsg = (f"{model} did not produce a usable build plan even after "
                    "loosening the output format — it may not support "
                    f"structured build output. Try -m {DEFAULT_CODE_MODEL} "
                    "(reliable for builds), or rephrase the task.")
            if _json_mode(args):
                emit_json_error("build", "model", pmsg, api_key)
            sys.exit(f"ambient: {pmsg}")
        plan, bad = _build_workflow.validate_plan_items(
            obj["plan"], max_files=args.max_files, root=root,
            safe_relpath=safe_relpath)
        if len(obj["plan"]) > args.max_files:
            print(f"ambient: plan capped at {args.max_files} files "
                  f"(model proposed {len(obj['plan'])}; raise with "
                  "--max-files)", file=sys.stderr)
        # Learn AFTER path validation (Codex: recording True before validation
        # marked a model capable when every path was unsafe → build unusable).
        record_cap(served_model, "build_plan", bool(plan))
        if not plan:
            rmsg = ("every planned path was rejected as unsafe — not "
                    "proceeding. (This usually means a poisoned or confused "
                    "reply; try again.)")
            if _json_mode(args):
                emit_json_error("build", "model", rmsg, api_key)
            sys.exit(f"ambient: {rmsg}")
        state = {"version": 1, "task_sha": task_sha, "plan": plan,
                 "notes": str(obj.get("notes", ""))[:2000],
                 "advisory_steps": [str(s)[:300] for s in
                                    (obj.get("advisory_steps") or [])[:20]],
                 "done": {}, "failed": bad}
        _save_build_state(root, state)

    plan = state["plan"]
    plan_paths = {p["path"] for p in plan}   # the reviewed plan is a CONTRACT
    advisory = state.get("advisory_steps") or []
    if getattr(args, "plan_only", False):
        if args.json:
            po_failed = state.get("failed") or []
            emit_json("build", model=model, api_key=api_key,
                      partial=bool(po_failed),
                      reason=(f"{len(po_failed)} planned path(s) rejected: "
                              + "; ".join(f"{f['path']} ({f['reason']})"
                                          for f in po_failed[:10])
                              if po_failed else None),
                      allow_partial=getattr(args, "allow_partial", False),
                      extra={"root": root, "plan": plan,
                             "notes": state.get("notes", ""),
                             "advisory_steps": advisory,
                             "failed": po_failed,
                             "written": False})
            return
        print(f"Build plan for: {task}\n")
        for item in plan:
            print(f"  {item['path']:44} ~{item.get('est_lines', '?')} lines  "
                  f"{item.get('purpose', '')}")
        if state.get("notes"):
            print(f"\n{state['notes']}")
        print("\nGenerate with the same command minus --plan-only "
              "(the plan is cached — planning is not re-billed).")
        return

    # ---- PHASE 2: GENERATE (batched, truncation-safe) ----------------------
    done = dict(state["done"])   # path -> {"content":…, "sha256":…}
    failed = [f for f in (state.get("failed") or [])
              if str(f.get("reason", "")).startswith("unsafe path")]
    # Paths the model returned that are NOT in the approved plan: never written,
    # surfaced as a warning, but NOT counted as a failure — a chatty extra file
    # must not turn a fully-delivered plan into a partial/exit-2 result.
    dropped = []
    # Bounded retry tree: splits + per-file retries can multiply calls far past
    # len(batches), so the gate must price the CAP, and the loop must enforce
    # it — a model that keeps omitting files can't drive unbounded spend
    #.
    batches, max_gen_calls = _build_workflow.generation_batches(
        plan, done_paths=done, max_tokens=args.max_tokens,
        chars_per_token=CHARS_PER_TOKEN)
    gen_calls = 0
    # Generation is a stream of per-file JSON objects, not one JSON object. A
    # response_format schema would force the old wrapper shape and make
    # truncation recovery unsafe, so generation stays prompt-only.
    rf_files = None
    gen_extra = ""
    attempts = {}
    recovery_paths = set()
    queue = list(batches)
    while queue:
        batch = queue.pop(0)
        if gen_calls >= max_gen_calls:
            for p in batch:
                if p["path"] not in done:
                    failed.append({"path": p["path"],
                                   "reason": "generation call budget exhausted "
                                             "(bounded spend) — re-run to "
                                             "resume where this stopped"})
            continue
        want = [p["path"] for p in batch]
        # M44: keep the generation prompt within the model's single-shot window.
        # Splitting the BATCH can't help (task/plan/already/context are
        # batch-independent), so shrink the INPUT: drop the already-list, then
        # truncate context, then condense the plan to paths only.
        user = _build_workflow.generation_prompt(
            task=task, batch=batch, plan=plan, done_paths=done,
            context=context, system_chars=len(BUILD_GEN_PROMPT + gen_extra),
            single_shot_chars=single, recovery_paths=recovery_paths)
        if user is None:
            # Still over even minimized. Split a multi-file batch BEFORE the API
            # call (avoids a doomed 400 round-trip); a single-file batch that
            # can't fit is an honest, non-splittable failure.
            if len(batch) > 1:
                mid = len(batch) // 2
                queue.insert(0, batch[mid:])
                queue.insert(0, batch[:mid])
                continue
            failed.append({"path": want[0],
                           "reason": "prompt exceeds the model's context window "
                                     "even minimized — split the task or use a "
                                     "larger-context -m model"})
            continue
        ga, _x = _ns_with(rf_files)
        generation_chars = len(BUILD_GEN_PROMPT + gen_extra) + len(user)
        if getattr(args, "_auto_budget", False):
            # The command-level budget was sized against the profile's
            # worst-case single-shot input. This concrete file request is often
            # far smaller, so re-derive its context-safe ceiling before asking
            # the model to spend more reasoning on a complete artifact.
            ga.max_tokens = None
            apply_output_budget(ga, profile, generation_chars)
        else:
            ga.escalation_ceiling = max(
                ga.max_tokens,
                _context_safe_escalation_ceiling(
                    profile, generation_chars, _effective_cpt(profile.model)))
        ga.max_budget_escalations = MAX_BUDGET_ESCALATIONS
        gen_calls += 1
        # C1: name WHICH files this call generates and how far along the plan we
        # are, on top of the streaming char-heartbeat — a long build must show
        # progress, never look stuck. stderr only; --json stdout is untouched.
        # Silenced by --no-progress / AMBIENT_PROGRESS=off (error/retry lines
        # below are diagnostics, not progress, so they are NEVER silenced).
        if progress_display_enabled():
            print(f"ambient: generating {', '.join(want)}  "
                  f"[{len(done)}/{len(plan_paths)} of the plan done]",
                  file=sys.stderr)
        try:
            content, _u, body = complete(
                api_key, api_url, model,
                [{"role": "system", "content": BUILD_GEN_PROMPT + gen_extra},
                 {"role": "user", "content": user}], ga, session=session)
        except ChatError as err:
            if len(batch) > 1:
                mid = len(batch) // 2
                queue.insert(0, batch[mid:])
                queue.insert(0, batch[:mid])
                print(f"ambient: batch of {len(batch)} failed "
                      f"([{err.category}]) — retrying as two smaller batches",
                      file=sys.stderr)
                continue
            failed.append({"path": want[0],
                           "reason": f"[{err.category}] {err.diagnosis}"[:300]})
            continue
        if isinstance(body, dict) and body.get("reasoning_draft"):
            records = []
        else:
            records = _parse_file_records(content)
        accepted, rejected, unplanned = _build_workflow.classify_file_records(
            records, wanted_paths=want, plan_paths=plan_paths,
            done_paths=done, root=root, max_file_bytes=args.max_file_bytes,
            salvaged_partial=(isinstance(body, dict)
                               and bool(body.get("salvaged_partial"))),
            safe_relpath=safe_relpath)
        got = dict(accepted)
        failed.extend({"path": path, "reason": reason}
                      for path, reason in rejected)
        dropped.extend(path for path in unplanned if path not in dropped)
        if failed and got:
            failed = [item for item in failed if item.get("path") not in got]
        for rel, body_text in got.items():
            done[rel] = {"content": body_text,
                         "sha256": hashlib.sha256(body_text.encode()).hexdigest()}
        recovery_paths.difference_update(got)
        state["done"] = done
        state["failed"] = failed
        _save_build_state(root, state)
        retries, next_attempts, next_recovery, retry_failures = (
            _build_workflow.generation_recovery(
                batch, done_paths=done,
                failed_paths=(item["path"] for item in failed),
                response_meta=body, attempts=attempts,
                recovery_paths=recovery_paths))
        attempts = dict(next_attempts)
        recovery_paths = set(next_recovery)
        failed.extend({"path": path, "reason": reason}
                      for path, reason in retry_failures)
        if retries:
            queue[0:0] = list(retries)
            retry_count = sum(len(part) for part in retries)
            if len(batch) > 1:
                print(f"ambient: {retry_count} file(s) missing/cut from that "
                      "reply — regenerating in smaller batches "
                      "(input re-billed)", file=sys.stderr)
            else:
                print(f"ambient: {retries[0][0]['path']} came back incomplete "
                      "— one more attempt", file=sys.stderr)
    state["failed"] = failed
    _save_build_state(root, state)

    # ---- EMIT + optional APPLY ---------------------------------------------
    partial = bool(failed)
    total_bytes = sum(len(v["content"].encode()) for v in done.values())
    written = False
    actions = {}
    if args.apply and done:
        proceed = True
        if not headless and not args.yes:
            try:
                ans = input(f"Write {len(done)} file(s) ({total_bytes:,} bytes) "
                            f"into {root}? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            proceed = ans in ("y", "yes")
        if proceed:
            applied, apply_failures = _build_apply.apply_records(
                done, root, force=args.force,
                backup_stamp=time.strftime("%Y%m%d-%H%M%S"),
                within_root=_within_root)
            actions = dict(applied)
            failed.extend({"path": path, "reason": reason}
                          for path, reason in apply_failures)
            if apply_failures or any(
                    action == "skip-exists" for action in actions.values()):
                partial = True
            # "written" == at least one file actually landed with the intended
            # content (freshly created/overwritten, or already-present-and-
            # matching). If every file skip-exists'd or write-failed, nothing
            # landed and the header must NOT claim WRITTEN.
            written = any(a in ("create", "overwrite", "unchanged")
                          for a in actions.values())
        else:
            print("ambient: not written (declined) — the manifest below is "
                  "cached in .ambient-build.json; re-run with --apply --yes "
                  "to write without asking", file=sys.stderr)
    files_out = [{"path": rel, "bytes": len(rec["content"].encode()),
                  "sha256": rec["sha256"],
                  "action": actions.get(rel, "generated" if not written
                                        else "create")}
                 for rel, rec in sorted(done.items())]
    reason = None
    if failed:
        reason = f"{len(failed)} file(s) failed: " + "; ".join(
            f"{f['path']} ({f['reason']})" for f in failed[:10])
    elif partial:
        # partial with no hard failures = files skipped because they already
        # exist and --force was not given. A partial must never be reason-less.
        skipped = [r for r, a in actions.items() if a == "skip-exists"]
        if skipped:
            reason = (f"{len(skipped)} file(s) already exist and were left "
                      "unchanged (pass --force to overwrite): "
                      + ", ".join(skipped[:10]))
    if args.json:
        emit_json("build", model=model, api_key=api_key,
                  partial=partial, reason=reason,
                  allow_partial=getattr(args, "allow_partial", False),
                  extra={"root": root, "files": files_out, "failed": failed,
                         "dropped": dropped,
                         "advisory_steps": advisory,
                         "notes": state.get("notes", ""),
                         "written": written, "total_bytes": total_bytes})
        return
    print(f"\nBuild result for: {task}")
    if written:
        target_state = "WRITTEN"
    elif getattr(args, "apply", False):
        # --apply WAS passed but nothing landed — telling the user to "re-run
        # with --apply" they already passed is nonsense; say WHY instead. Files
        # can be missing because they failed (validation/write) OR were skipped
        # (already present without --force); the per-file lines below show which.
        target_state = (f"nothing written — {len(failed)} file(s) failed"
                        if failed else "nothing written")
    else:
        target_state = "manifest only — re-run with --apply to write"
    print(f"  target: {root} ({target_state})")
    for f in files_out:
        print(f"  {f['action']:>11}  {f['path']:44} {f['bytes']:>8,} bytes")
    for f in failed:
        print(redact(f"       FAILED  {f['path']:44} {f['reason']}", api_key))
    for d in dropped:
        print(f"      DROPPED  {d:44} not in the approved plan (not written)")
    if state.get("notes"):
        print(f"\n{redact(state['notes'], api_key)}")
    if advisory:
        print("\nSuggested next steps (NOT executed — review first):")
        for s in advisory:
            print(redact(f"  {s}", api_key))
    if partial and not getattr(args, "allow_partial", False):
        sys.exit(EXIT_PARTIAL)



__all__ = ("BuildDependencies", "run_build")
