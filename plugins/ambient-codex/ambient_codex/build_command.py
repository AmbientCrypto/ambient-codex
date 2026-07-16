"""Model-aware, resumable build command orchestration."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from . import build_generation as _build_generation
from . import build_preview as _build_preview
from . import build_reporting as _build_reporting


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
    """Run a capacity-safe, resumable plan → generate → report build."""
    BUILD_FAILURE_CIRCUIT_BREAKER = deps.BUILD_FAILURE_CIRCUIT_BREAKER
    BUILD_JSON_INSTRUCTION_PLAN = deps.BUILD_JSON_INSTRUCTION_PLAN
    BUILD_PLAN_PROMPT = deps.BUILD_PLAN_PROMPT
    ChatError = deps.ChatError
    DEFAULT_CODE_MODEL = deps.DEFAULT_CODE_MODEL
    DIRECT_BUILD_BATCH_FILES = deps.DIRECT_BUILD_BATCH_FILES
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    MAX_BUILD_FILES = deps.MAX_BUILD_FILES
    MAX_BUILD_FILE_BYTES = deps.MAX_BUILD_FILE_BYTES
    REASONING_BUILD_BATCH_FILES = deps.REASONING_BUILD_BATCH_FILES
    Session = deps.Session
    _build_apply = deps._build_apply
    _build_workflow = deps._build_workflow
    _effective_cpt = deps._effective_cpt
    _fail = deps._fail
    _fail_exit = deps._fail_exit
    _json_mode = deps._json_mode
    _load_build_state = deps._load_build_state
    _parse_file_records = deps._parse_file_records
    _resolve_parallel = deps._resolve_parallel
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
    extract_json = deps.extract_json
    files_block = deps.files_block
    is_auto_model = deps.is_auto_model
    model_profile = deps.model_profile
    note_if_hidden = deps.note_if_hidden
    pack_chunks = deps.pack_chunks
    progress_display_enabled = deps.progress_display_enabled
    read_files = deps.read_files
    record_cap = deps.record_cap
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
    # Reject invalid caps before resume, catalog access, or spend.
    if args.max_files < 1:
        usage_exit("--max-files must be a positive integer")
    if args.max_files > MAX_BUILD_FILES:
        usage_exit(
            f"--max-files must be at most {MAX_BUILD_FILES} "
            "(split larger programs into scoped builds)")
    if args.max_file_bytes < 1:
        usage_exit("--max-file-bytes must be a positive integer")
    if args.max_file_bytes > MAX_BUILD_FILE_BYTES:
        usage_exit(
            f"--max-file-bytes must be at most "
            f"{MAX_BUILD_FILE_BYTES:,}")
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
    # Dry-run skips the live catalog so its zero-egress contract is literal.
    catalog = [] if getattr(args, "dry_run", False) else session.catalog()
    ctx_probe = 0  # cheap size probe (stat only) for routing
    for p in (args.context or []):
        try:
            ctx_probe += os.path.getsize(p)
        except OSError:
            pass
    if getattr(args, "dry_run", False):
        # An auto lane cannot resolve without a catalog; use its preview default.
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
    model_cpt = _effective_cpt(profile.model)
    structured_output = bool(
        {"structured_outputs", "json_mode"} & set(profile.features or []))
    target_file_bytes = _build_workflow.artifact_target_bytes(
        max_tokens=args.max_tokens, chars_per_token=model_cpt,
        max_file_bytes=args.max_file_bytes,
        is_reasoning=profile.is_reasoning,
        structured_output=structured_output)

    context = ""
    # Raw-context identity invalidates resume state after any -f edit.
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
            packed = pack_chunks(chunks,
                                 max(MIN_REASONING_CHUNK,
                                     int(profile.chunk_chars
                                         / density_factor(context))))

    # DRY RUN spends nothing and egresses nothing — decide + return BEFORE the
    # context-distillation map-reduce, which makes billed calls and sends file
    # contents to the network. A flag that says "nothing sent" must send nothing.
    if getattr(args, "dry_run", False):
        _build_preview.emit_build_preview(
            _build_preview.BuildPreview(
                args=args, catalog=tuple(catalog), model=model,
                reduce_model=reduce_model, task=task, context=context,
                root=root, profile=profile, needs_distill=needs_distill,
                packed=tuple(packed or ()), single_shot_chars=single,
                target_file_bytes=target_file_bytes,
                dry_run_auto_model=dry_run_auto_model,
                conf=conf,
            ),
            deps,
        )
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
        user = (
            f"TASK: {task}\n\nBUILD CAPACITY CONTRACT:\n"
            f"- Plan no more than {args.max_files} files.\n"
            f"- Estimate est_bytes honestly for every file.\n"
            f"- Keep each file at or below {target_file_bytes:,} estimated "
            f"bytes so one focused model answer can finish it.\n"
            f"- The absolute hard cap is {args.max_file_bytes:,} bytes/file.\n"
            "- Decompose large pages, decks, stylesheets, datasets, and "
            "generated documents into composable files.\n"
            "- Use workstream and depends_on to preserve generation order.\n"
            "- Put exact shared names, interfaces, routes, design tokens, "
            "claims/disclaimers, and acceptance constraints in project_brief."
        )
        if context:
            user += f"\n\nContext files:\n\n{context}"
        base_user = user

        def _request_plan(plan_user):
            obj, served_model = None, model
            # Each attempt DOWNGRADES the structured-output demand rather than
            # re-sending the same rejected request shape.
            attempts = _build_workflow.prioritize_plan_formats(
                build_plan_rf_ladder(model, profile),
                input_chars=len(plan_user))
            current_user = plan_user
            for attempt, attempt_rf in enumerate(attempts, 1):
                pa, extra = _ns_with(
                    attempt_rf,
                    "" if (attempt_rf
                           and attempt_rf.get("type") == "json_schema")
                    else BUILD_JSON_INSTRUCTION_PLAN)
                pa.max_budget_escalations = 0
                try:
                    content, _u, _b = complete(
                        api_key, api_url, model,
                        [{"role": "system",
                          "content": BUILD_PLAN_PROMPT + extra},
                         {"role": "user", "content": current_user}],
                        pa, session=session)
                except ChatError as err:
                    if err.category != "unknown" or attempt == len(attempts):
                        _fail(
                            args, "build",
                            ChatError(
                                err.category,
                                f"planning failed — {err.diagnosis}"),
                            api_key)
                    continue
                served_model = _served_model_of(_b, model)
                finish_reason = (
                    str(_b.get("finish_reason") or "").lower()
                    if isinstance(_b, dict) else "")
                dirty = (
                    isinstance(_b, dict)
                    and (_b.get("reasoning_draft")
                         or _b.get("salvaged_partial"))
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
                if not dirty:
                    obj = extract_json(content)
                    if obj and obj.get("_repaired"):
                        obj = None
                if obj and isinstance(obj.get("plan"), list) and obj["plan"]:
                    return obj, served_model
                obj = None
                if attempt < len(attempts):
                    print(
                        "ambient: plan reply wasn't valid JSON — retrying "
                        "with a looser output format",
                        file=sys.stderr,
                    )
                    current_user += (
                        "\n\nREMINDER: return ONLY the JSON object, no prose, "
                        "no fences.")
            return None, served_model

        obj, served_model, contract = None, model, None
        plan_user = base_user
        for planning_round in range(2):
            obj, served_model = _request_plan(plan_user)
            if not (obj and isinstance(obj.get("plan"), list)
                    and obj["plan"]):
                break
            contract = _build_workflow.assess_plan_contract(
                obj["plan"], max_files=args.max_files,
                max_file_bytes=args.max_file_bytes,
                target_file_bytes=target_file_bytes, root=root,
                safe_relpath=safe_relpath)
            if contract["complete"]:
                break
            if any(
                    str(item.get("reason", "")).startswith("unsafe path")
                    for item in contract["rejected"]):
                # A path-firewall violation is a security boundary, not a
                # capacity-shaping problem. Fail closed without sending the
                # rejected plan back through another model call.
                break
            if planning_round == 0:
                issues = "; ".join(
                    f"{item['path'] or '(missing path)'}: {item['reason']}"
                    for item in contract["failures"][:20])
                print(
                    "ambient: plan exceeds the safe build contract — asking "
                    "the model to decompose it before generation",
                    file=sys.stderr,
                )
                prior = json.dumps(obj, ensure_ascii=False)[:40_000]
                plan_user = (
                    f"{base_user}\n\nREPLAN REQUIRED. The prior proposal cannot "
                    f"be generated safely:\n{issues}\n\nRedesign the complete "
                    "project within every capacity rule. Preserve the same "
                    "project identity and requirements, but split oversized "
                    f"artifacts and repair plan defects.\n\nPRIOR PLAN:\n{prior}"
                )
        if not (obj and isinstance(obj.get("plan"), list) and obj["plan"]):
            record_cap(served_model, "build_plan", False)
            pmsg = (f"{model} did not produce a usable build plan even after "
                    "loosening the output format — it may not support "
                    f"structured build output. Try -m {DEFAULT_CODE_MODEL} "
                    "(reliable for builds), or rephrase the task.")
            if _json_mode(args):
                emit_json_error("build", "model", pmsg, api_key)
            sys.exit(f"ambient: {pmsg}")
        contract = contract or _build_workflow.assess_plan_contract(
            obj["plan"], max_files=args.max_files,
            max_file_bytes=args.max_file_bytes,
            target_file_bytes=target_file_bytes, root=root,
            safe_relpath=safe_relpath)
        record_cap(served_model, "build_plan", contract["complete"])
        plan = list(
            contract["plan"] if contract["complete"]
            else contract["runnable_plan"])
        coverage_failures = [
            dict(item) for item in contract["failures"]]
        if not plan:
            rmsg = ("every planned path was rejected as unsafe — not "
                    "proceeding. The planner could not produce a runnable "
                    "capacity-safe artifact set.")
            if contract["failures"]:
                rmsg += " " + "; ".join(
                    f"{item['path']} ({item['reason']})"
                    for item in contract["failures"][:10])
            if _json_mode(args):
                emit_json_error("build", "model", rmsg, api_key)
            sys.exit(f"ambient: {rmsg}")
        if not contract["complete"] and (
                getattr(args, "plan_only", False)
                or not getattr(args, "allow_partial", False)):
            reason = (
                "planning coverage is incomplete: "
                + "; ".join(
                    f"{item['path']} ({item['reason']})"
                    for item in coverage_failures[:10])
            )
            if args.json:
                emit_json(
                    "build", model=model, api_key=api_key, partial=True,
                    reason=reason,
                    allow_partial=getattr(args, "allow_partial", False),
                    extra={
                        "root": root,
                        "plan": list(contract["plan"]),
                        "project_brief": str(
                            obj.get("project_brief", ""))[:6_000],
                        "notes": str(obj.get("notes", ""))[:2_000],
                        "advisory_steps": [
                            str(step)[:300] for step in
                            (obj.get("advisory_steps") or [])[:20]],
                        "failed": coverage_failures,
                        "written": False,
                        "target_file_bytes": target_file_bytes,
                    },
                )
                return
            print(f"ambient: {reason}", file=sys.stderr)
            sys.exit(EXIT_PARTIAL)
        state = {"version": 1, "task_sha": task_sha, "plan": plan,
                 "project_brief": str(obj.get("project_brief", ""))[:6000],
                 "notes": str(obj.get("notes", ""))[:2000],
                 "advisory_steps": [str(s)[:300] for s in
                                    (obj.get("advisory_steps") or [])[:20]],
                 "target_file_bytes": target_file_bytes,
                 "done": {}, "failed": coverage_failures,
                 "plan_failures": coverage_failures,
                 "metrics": {}}
        _save_build_state(root, state)

    plan = state["plan"]
    plan_paths = {p["path"] for p in plan}   # the reviewed plan is a CONTRACT
    advisory = state.get("advisory_steps") or []
    if getattr(args, "plan_only", False):
        _build_reporting.emit_plan_only(
            args=args, model=model, api_key=api_key, root=root, task=task,
            state=state, plan=plan, advisory=advisory,
            target_file_bytes=target_file_bytes, deps=deps)
        return

    # ---- PHASE 2: GENERATE (batched, truncation-safe) ----------------------
    done = dict(state["done"])   # path -> {"content":…, "sha256":…}
    plan_failures = [
        dict(item) for item in (state.get("plan_failures") or [])]
    failed = plan_failures + [
        f for f in (state.get("failed") or [])
        if str(f.get("reason", "")).startswith("unsafe path")
        and f not in plan_failures]
    # Paths the model returned that are NOT in the approved plan: never written,
    # surfaced as a warning, but NOT counted as a failure — a chatty extra file
    # must not turn a fully-delivered plan into a partial/exit-2 result.
    dropped = []
    actions = {}
    backup_stamp = time.strftime("%Y%m%d-%H%M%S")
    incremental_apply = bool(args.apply and headless and args.yes)

    def _merge_actions(applied):
        for relative, action in applied:
            if actions.get(relative) in ("create", "overwrite") \
                    and action == "unchanged":
                continue
            actions[relative] = action

    if incremental_apply and done:
        applied, apply_warnings = _build_apply.apply_records(
            done, root, force=args.force, backup_stamp=backup_stamp,
            within_root=_within_root)
        _merge_actions(applied)
        for path, reason in apply_warnings:
            print(f"ambient: incremental write failed for {path}: {reason}",
                  file=sys.stderr)
    # Price and enforce the complete split/retry call ceiling.
    batches, max_gen_calls = _build_workflow.generation_batches(
        plan, done_paths=done, max_tokens=args.max_tokens,
        chars_per_token=model_cpt,
        target_batch_bytes=state.get(
            "target_file_bytes", target_file_bytes),
        max_batch_files=(
            REASONING_BUILD_BATCH_FILES
            if profile.is_reasoning else DIRECT_BUILD_BATCH_FILES))
    gen_calls = 0
    # Multi-file generation stays record-framed; focused calls may be structured.
    attempts = {}
    recovery_paths = set()
    queue = list(batches)
    consecutive_no_usable = 0
    parallel_width = _resolve_parallel(args)

    def _generation_request(batch, done_snapshot, attempts_snapshot,
                            recovery_snapshot):
        return _build_generation.GenerationRequest(
            args=args, api_key=api_key, api_url=api_url, model=model,
            profile=profile, model_cpt=model_cpt,
            single_shot_chars=single, task=task, context=context,
            plan=tuple(plan), batch=tuple(batch),
            done_paths=tuple(done_snapshot),
            attempts=tuple(attempts_snapshot.items()),
            recovery_paths=tuple(recovery_snapshot),
            project_brief=state.get("project_brief", ""),
            session=session,
        )

    while queue:
        if gen_calls >= max_gen_calls:
            exhausted = {
                item["path"] for batch in queue for item in batch
                if item["path"] not in done
            }
            failed.extend({
                "path": path,
                "reason": "generation call budget exhausted (bounded spend) "
                          "— re-run to resume where this stopped",
            } for path in sorted(exhausted))
            queue.clear()
            break
        failed_paths = {item["path"] for item in failed}
        width = min(parallel_width, max_gen_calls - gen_calls)
        wave, pending_queue, dependency_failures = (
            _build_workflow.ready_generation_wave(
                queue, done_paths=done, failed_paths=failed_paths,
                width=width))
        failed.extend({"path": path, "reason": reason}
                      for path, reason in dependency_failures)
        queue = list(pending_queue)
        if not wave:
            if queue:
                deadlocked = {
                    item["path"] for batch in queue for item in batch
                    if item["path"] not in done
                }
                failed.extend({
                    "path": path,
                    "reason": "dependency scheduler could not make progress",
                } for path in sorted(deadlocked))
                queue.clear()
            continue
        metrics = dict(state.get("metrics") or {})
        metrics["generation_waves"] = (
            int(metrics.get("generation_waves", 0)) + 1)
        state["metrics"] = metrics
        done_snapshot = tuple(done)
        attempts_snapshot = dict(attempts)
        recovery_snapshot = tuple(recovery_paths)
        if progress_display_enabled():
            durable = sum(
                action in ("create", "overwrite", "unchanged")
                for action in actions.values())
            for batch in wave:
                want = [item["path"] for item in batch]
                print(f"ambient: generating {', '.join(want)}  "
                      f"[{len(done)}/{len(plan_paths)} of the plan done; "
                      f"{durable} durable; wave ≤{len(wave)} parallel]",
                      file=sys.stderr)
        if len(wave) == 1:
            request = _generation_request(
                wave[0], done_snapshot, attempts_snapshot, recovery_snapshot)
            results = [_build_generation.generate_batch(request, deps)]
        else:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(wave)) as pool:
                futures = [
                    pool.submit(
                        _build_generation.generate_batch,
                        _generation_request(
                            batch, done_snapshot, attempts_snapshot,
                            recovery_snapshot),
                        deps,
                    )
                    for batch in wave
                ]
                results = [future.result() for future in futures]
        called_in_wave = sum(result.called for result in results)
        gen_calls += called_in_wave
        metrics = dict(state.get("metrics") or {})
        metrics["maximum_parallel_generation_calls"] = max(
            int(metrics.get("maximum_parallel_generation_calls", 0)),
            called_in_wave)
        state["metrics"] = metrics
        wave_retries = []
        usable_by_index = {}
        terminal_errors = []
        for index, result in enumerate(results):
            batch = result.batch
            want = list(result.wanted_paths)
            if result.prompt_too_large:
                if len(batch) > 1:
                    midpoint = len(batch) // 2
                    wave_retries.extend(
                        (index, part) for part in (
                            batch[:midpoint], batch[midpoint:]))
                else:
                    failed.append({
                        "path": want[0],
                        "reason": "prompt exceeds the model's context window "
                                  "even minimized — split the task or use a "
                                  "larger-context -m model",
                    })
                usable_by_index[index] = True
                continue
            content = result.content
            body = result.body
            error = result.error
            if error is not None:
                terminal_categories = {
                    "key", "funds", "rate", "model", "network", "service",
                }
                if error.category in terminal_categories:
                    terminal_errors.append(error)
                    usable_by_index[index] = True
                    continue
                if len(batch) > 1:
                    wave_retries.extend(
                        (index, (item,)) for item in batch)
                    usable_by_index[index] = True
                    print(f"ambient: batch of {len(batch)} failed "
                          f"([{error.category}]) — retrying as focused files",
                          file=sys.stderr)
                    continue
                body = {
                    "reasoning_draft": error.category == "empty",
                    "salvaged_partial": True,
                    "build_error_category": error.category,
                }
            records = (
                [] if isinstance(body, dict)
                and body.get("reasoning_draft")
                else _parse_file_records(content)
            )
            accepted, rejected, unplanned = (
                _build_workflow.classify_file_records(
                    records, wanted_paths=want, plan_paths=plan_paths,
                    done_paths=done, root=root,
                    max_file_bytes=args.max_file_bytes,
                    salvaged_partial=(
                        isinstance(body, dict)
                        and bool(body.get("salvaged_partial"))),
                    safe_relpath=safe_relpath))
            got = dict(accepted)
            usable_by_index[index] = bool(got)
            failed.extend({"path": path, "reason": reason}
                          for path, reason in rejected)
            dropped.extend(
                path for path in unplanned if path not in dropped)
            if failed and got:
                failed = [
                    item for item in failed
                    if item.get("path") not in got
                ]
            for relative, body_text in got.items():
                done[relative] = {
                    "content": body_text,
                    "sha256": hashlib.sha256(
                        body_text.encode()).hexdigest(),
                }
            recovery_paths.difference_update(got)
            metrics = dict(state.get("metrics") or {})
            metrics["logical_generation_calls"] = (
                int(metrics.get("logical_generation_calls", 0)) + 1)
            if isinstance(body, dict) and body.get("reasoning_draft"):
                metrics["reasoning_only_responses"] = (
                    int(metrics.get("reasoning_only_responses", 0)) + 1)
            if isinstance(body, dict) and (
                    body.get("finish_reason") == "length"
                    or body.get("salvaged_partial")):
                metrics["partial_responses"] = (
                    int(metrics.get("partial_responses", 0)) + 1)
            metrics["generated_files"] = len(done)
            state["done"] = done
            state["failed"] = failed
            state["metrics"] = metrics
            _save_build_state(root, state)
            if incremental_apply and got:
                records_to_apply = {
                    path: done[path] for path in got
                }
                applied, apply_warnings = _build_apply.apply_records(
                    records_to_apply, root, force=args.force,
                    backup_stamp=backup_stamp,
                    within_root=_within_root)
                _merge_actions(applied)
                for path, reason in apply_warnings:
                    print(
                        f"ambient: incremental write failed for {path}: "
                        f"{reason}",
                        file=sys.stderr,
                    )
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
            wave_retries.extend((index, retry) for retry in retries)
            if retries:
                metrics = dict(state.get("metrics") or {})
                metrics["recovery_batches"] = (
                    int(metrics.get("recovery_batches", 0))
                    + len(retries))
                state["metrics"] = metrics
                retry_count = sum(len(part) for part in retries)
                if len(batch) > 1:
                    print(
                        f"ambient: {retry_count} file(s) missing/cut from "
                        "that reply — regenerating in smaller batches "
                        "(input re-billed)",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"ambient: {retries[0][0]['path']} came back "
                        "incomplete — one more attempt",
                        file=sys.stderr,
                    )
                _save_build_state(root, state)
        for index in sorted(usable_by_index):
            consecutive_no_usable = (
                0 if usable_by_index[index]
                else consecutive_no_usable + 1)
        wave_retries.sort(key=lambda item: item[0])
        queue[0:0] = [batch for _index, batch in wave_retries]
        if terminal_errors:
            error = terminal_errors[0]
            reason = f"[{error.category}] {error.diagnosis}"[:300]
            current_failed = {item["path"] for item in failed}
            remaining = [
                path for path in plan_paths
                if path not in done and path not in current_failed
            ]
            failed.extend({"path": path, "reason": reason}
                          for path in remaining)
            metrics = dict(state.get("metrics") or {})
            metrics["terminal_build_aborts"] = (
                int(metrics.get("terminal_build_aborts", 0)) + 1)
            state["metrics"] = metrics
            queue.clear()
        elif consecutive_no_usable >= BUILD_FAILURE_CIRCUIT_BREAKER:
            current_failed = {item["path"] for item in failed}
            circuit_reason = (
                f"build stopped after {BUILD_FAILURE_CIRCUIT_BREAKER} "
                "consecutive model replies produced no usable planned file; "
                "resume later or choose another serving code model")
            remaining = [
                path for path in plan_paths
                if path not in done and path not in current_failed
            ]
            failed.extend({"path": path, "reason": circuit_reason}
                          for path in remaining)
            metrics = dict(state.get("metrics") or {})
            metrics["circuit_breaker_trips"] = (
                int(metrics.get("circuit_breaker_trips", 0)) + 1)
            state["metrics"] = metrics
            queue.clear()
            print(
                "ambient: repeated unusable file replies — stopping this "
                "bounded build before retries amplify",
                file=sys.stderr,
            )
        state["failed"] = failed
        _save_build_state(root, state)
    state["failed"] = failed
    _save_build_state(root, state)

    # ---- EMIT + optional APPLY ---------------------------------------------
    _build_reporting.emit_build_report(
        _build_reporting.BuildReport(
            args=args,
            task=task,
            model=model,
            api_key=api_key,
            root=root,
            state=state,
            done=done,
            failed=tuple(failed),
            dropped=tuple(dropped),
            actions=actions,
            advisory=tuple(advisory),
            headless=headless,
            backup_stamp=backup_stamp,
            target_file_bytes=target_file_bytes,
        ),
        deps,
    )



__all__ = ("BuildDependencies", "run_build")
