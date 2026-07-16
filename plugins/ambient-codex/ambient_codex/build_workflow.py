"""Bounded path, identity, and state policies for resumable build workflows."""

import hashlib
import json
import os
import re


ESTIMATED_BYTES_PER_LINE = 128
MIN_ARTIFACT_TARGET_BYTES = 8_000
REASONING_VISIBLE_OUTPUT_FRACTION = 0.50
DIRECT_VISIBLE_OUTPUT_FRACTION = 0.78
UNSTRUCTURED_OUTPUT_DISCOUNT = 0.85
JSON_RECORD_OVERHEAD = 1.15
REASONING_GENERATION_RESERVE_TOKENS = 4_096
MAX_BUILD_PLAN_OMISSION_DETAILS = 100
MAX_BUILD_DEPENDENCIES = 64
BROAD_PLAN_PROMPT_CHARS = 800
BUILD_METRIC_KEYS = frozenset({
    "logical_generation_calls",
    "reasoning_only_responses",
    "partial_responses",
    "generated_files",
    "recovery_batches",
    "terminal_build_aborts",
    "circuit_breaker_trips",
    "generation_waves",
    "maximum_parallel_generation_calls",
})


def state_path(root):
    """Return the fixed resume-state path beneath a validated build root."""
    return os.path.join(root, ".ambient-build.json")


def within_root(child_real, root_real):
    """Return whether a resolved path is inside a resolved build root."""
    try:
        return os.path.commonpath([child_real, root_real]) == root_real
    except ValueError:
        return False


def safe_relative_path(path, root, *, secret_name_re):
    """Firewall one untrusted manifest path before any build write."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("empty path")
    normalized = path.strip().replace("\\", "/")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in normalized):
        raise ValueError("control characters in path")
    if len(normalized) > 1024:
        raise ValueError("path too long")
    if (normalized.startswith("/") or os.path.isabs(normalized)
            or re.match(r"^[A-Za-z]:", normalized)):
        raise ValueError("absolute/drive path")
    parts = [segment for segment in normalized.split("/")
             if segment not in ("", ".")]
    if not parts:
        raise ValueError("empty path")
    for segment in parts:
        folded = segment.rstrip(". ").lower()
        if segment == ".." or folded == "..":
            raise ValueError("parent-directory escape")
        if segment.startswith("~"):
            raise ValueError("home-directory reference")
        if folded == ".git":
            raise ValueError(".git internals are off-limits")
        if folded.startswith(".ambient-build"):
            raise ValueError("reserved ambient-build name")
        if not folded:
            raise ValueError("empty path segment")
    if secret_name_re.search(parts[-1]):
        raise ValueError("credential-named file")
    root_real = os.path.realpath(root)
    destination = os.path.join(root_real, *parts)
    parent_real = os.path.realpath(os.path.dirname(destination))
    if not within_root(parent_real, root_real):
        raise ValueError("resolves outside the target directory (symlink escape)")
    if os.path.islink(destination):
        raise ValueError("destination is an existing symlink")
    return "/".join(parts)


def resume_identity(*, runtime_version, task, model, reduce_model, context_paths,
                    raw_context_sha, max_files, max_file_bytes, max_tokens,
                    temperature):
    """Hash stable build inputs so incompatible resumptions are rejected."""
    payload = {"identity_version": 3, "runtime_version": runtime_version,
               "task": task, "model": model, "reduce_model": reduce_model,
               "context_paths": sorted(str(path) for path in (context_paths or [])),
               "raw_context_sha": raw_context_sha, "max_files": max_files,
               "max_file_bytes": max_file_bytes, "max_tokens": max_tokens,
               "temperature": temperature}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_plan(state, root, max_plan, safe_relpath):
    """Copy and firewall the bounded plan from untrusted resume state."""
    plan = []
    for item in state["plan"][:max_plan]:
        if not isinstance(item, dict):
            return None
        copied = dict(item)
        copied["path"] = safe_relpath(str(copied.get("path", "")), root)
        plan.append(copied)
    return plan


def _normalized_done(state, plan_paths, root, max_file_bytes, safe_relpath):
    """Return verified completed files that remain inside the bounded plan."""
    done = {}
    for path, record in state["done"].items():
        rel = safe_relpath(str(path), root)
        if rel not in plan_paths:
            continue
        if not (isinstance(record, dict) and isinstance(record.get("content"), str)):
            return None
        content = record["content"]
        if max_file_bytes is not None and len(content.encode()) > max_file_bytes:
            continue
        digest = hashlib.sha256(content.encode()).hexdigest()
        if record.get("sha256") != digest:
            return None
        done[rel] = {"content": content, "sha256": digest}
    return done


def _normalized_failures(value, limit):
    if not isinstance(value, list):
        return []
    failures = []
    for item in value[:limit]:
        if not (isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("reason"), str)):
            continue
        failures.append({
            "path": item["path"][:1_024],
            "reason": item["reason"][:1_000],
        })
    return failures


def _normalized_metrics(value):
    if not isinstance(value, dict):
        return {}
    metrics = {}
    for key in BUILD_METRIC_KEYS:
        raw = value.get(key)
        if isinstance(raw, bool):
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed >= 0:
            metrics[key] = parsed
    return metrics


def normalize_resume_state(state, *, task_sha, root, max_plan, max_file_bytes,
                           safe_relpath):
    """Validate parsed untrusted resume state without mutating its caller.

    ``safe_relpath`` is injected so the path firewall stays owned by the facade
    while this module remains pure and directly unit-testable.  Its ValueError
    is intentionally propagated so the boundary can emit the established warning.
    """
    if not (isinstance(state, dict) and state.get("version") == 1
            and state.get("task_sha") == task_sha
            and isinstance(state.get("plan"), list)
            and isinstance(state.get("done"), dict)):
        return None
    plan = _normalized_plan(state, root, max_plan, safe_relpath)
    if plan is None:
        return None
    done = _normalized_done(state, {item["path"] for item in plan}, root,
                            max_file_bytes, safe_relpath)
    if done is None:
        return None
    hard_cap = max_file_bytes or 200_000
    target_file_bytes = min(
        hard_cap,
        _positive_int(state.get("target_file_bytes"), hard_cap),
    )
    project_brief = state.get("project_brief")
    return dict(state, plan=plan, done=done,
                failed=_normalized_failures(
                    state.get("failed"), max(1, max_plan * 3)),
                plan_failures=_normalized_failures(
                    state.get("plan_failures"), max(1, max_plan * 2)),
                metrics=_normalized_metrics(state.get("metrics")),
                project_brief=(
                    project_brief[:6_000]
                    if isinstance(project_brief, str) else ""),
                target_file_bytes=target_file_bytes)


def validate_plan_items(items, *, max_files, root, safe_relpath):
    """Copy, cap, and firewall a model-proposed build manifest."""
    plan, rejected = [], []
    for item in items[:max_files]:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        candidate = dict(item)
        raw_path = str(candidate["path"])
        try:
            candidate["path"] = safe_relpath(raw_path, root)
            plan.append(candidate)
        except ValueError as error:
            rejected.append({"path": raw_path, "reason": f"unsafe path: {error}"})
    return plan, rejected


def estimated_item_bytes(item):
    """Conservatively estimate one planned artifact's encoded content size."""
    if isinstance(item, dict):
        explicit = _positive_int(item.get("est_bytes"), 0)
        if explicit:
            return max(512, explicit)
        lines = _positive_int(item.get("est_lines"), 50)
    else:
        lines = 50
    return max(512, lines * ESTIMATED_BYTES_PER_LINE)


def artifact_target_bytes(*, max_tokens, chars_per_token, max_file_bytes,
                          is_reasoning=False, structured_output=False):
    """Return a safe visible-artifact target beneath the total output budget."""
    tokens = _positive_int(max_tokens, 1)
    try:
        cpt = max(1.0, float(chars_per_token))
    except (TypeError, ValueError):
        cpt = 3.2
    hard_cap = _positive_int(max_file_bytes, 200_000)
    fraction = (
        REASONING_VISIBLE_OUTPUT_FRACTION
        if is_reasoning else DIRECT_VISIBLE_OUTPUT_FRACTION)
    if not structured_output:
        fraction *= UNSTRUCTURED_OUTPUT_DISCOUNT
    visible_target = int(tokens * cpt * fraction / JSON_RECORD_OVERHEAD)
    return min(hard_cap, max(MIN_ARTIFACT_TARGET_BYTES, visible_target))


def generation_output_tokens(*, estimated_bytes, chars_per_token,
                             is_reasoning=False, max_tokens=None):
    """Budget for escaped visible content plus bounded reasoning headroom."""
    try:
        cpt = max(1.0, float(chars_per_token))
    except (TypeError, ValueError):
        cpt = 3.2
    visible = int(
        max(0, estimated_bytes) * JSON_RECORD_OVERHEAD / cpt) + 1
    reserve = 0
    if is_reasoning:
        available = _positive_int(max_tokens, 0)
        reserve = (
            min(16_384, max(
                REASONING_GENERATION_RESERVE_TOKENS,
                int(available * 0.25)))
            if available else REASONING_GENERATION_RESERVE_TOKENS
        )
    return visible + reserve


def prioritize_plan_formats(ladder, *, input_chars):
    """Prefer looser JSON mode for broad plans with large structured payloads."""
    ordered = tuple(ladder)
    if input_chars < BROAD_PLAN_PROMPT_CHARS:
        return ordered
    json_object = tuple(
        item for item in ordered
        if isinstance(item, dict) and item.get("type") == "json_object")
    schema = tuple(
        item for item in ordered
        if isinstance(item, dict) and item.get("type") == "json_schema")
    other = tuple(
        item for item in ordered
        if item is None or (
            isinstance(item, dict)
            and item.get("type") not in {"json_object", "json_schema"}))
    return json_object + schema + other if json_object else ordered


def _normalized_dependencies(candidate, root, safe_relpath):
    raw = candidate.get("depends_on")
    if not isinstance(raw, list):
        return []
    dependencies = []
    for value in raw[:MAX_BUILD_DEPENDENCIES]:
        try:
            dependency = safe_relpath(str(value), root)
        except ValueError:
            dependency = str(value).strip()
        if dependency and dependency not in dependencies:
            dependencies.append(dependency)
    return dependencies


def _stable_topological_order(plan):
    """Return a stable dependency order plus paths participating in cycles."""
    by_path = {item["path"]: item for item in plan}
    original_order = {item["path"]: index for index, item in enumerate(plan)}
    incoming = {
        path: {dep for dep in item.get("depends_on", []) if dep in by_path}
        for path, item in by_path.items()
    }
    ready = sorted(
        (path for path, deps in incoming.items() if not deps),
        key=original_order.get,
    )
    ordered = []
    while ready:
        path = ready.pop(0)
        ordered.append(by_path[path])
        for candidate in by_path:
            if path not in incoming[candidate]:
                continue
            incoming[candidate] = incoming[candidate] - {path}
            if not incoming[candidate] and candidate not in {
                    item["path"] for item in ordered} and candidate not in ready:
                ready.append(candidate)
                ready.sort(key=original_order.get)
    cycles = tuple(
        path for path in by_path
        if path not in {item["path"] for item in ordered}
    )
    ordered.extend(by_path[path] for path in cycles)
    return tuple(ordered), cycles


def assess_plan_contract(items, *, max_files, max_file_bytes,
                         target_file_bytes, root, safe_relpath):
    """Validate a complete model plan before any cap or generation is hidden."""
    proposed = items if isinstance(items, list) else list(items)
    omitted_count = max(0, len(proposed) - max_files)
    omitted = tuple(proposed[
        max_files:max_files + MAX_BUILD_PLAN_OMISSION_DETAILS])
    bounded = proposed[:max_files]
    plan, rejected, duplicates, seen = [], [], [], set()
    for raw in bounded:
        if not isinstance(raw, dict):
            rejected.append({
                "path": "",
                "reason": "plan item must be an object",
            })
            continue
        raw = dict(raw)
        if not raw.get("path"):
            rejected.append({"path": "", "reason": "missing path"})
            continue
        candidate = dict(raw)
        raw_path = str(candidate["path"])
        try:
            path = safe_relpath(raw_path, root)
        except ValueError as error:
            rejected.append({
                "path": raw_path,
                "reason": f"unsafe path: {error}",
            })
            continue
        if path in seen:
            duplicates.append(path)
            rejected.append({"path": path, "reason": "duplicate planned path"})
            continue
        seen.add(path)
        candidate["path"] = path
        candidate["purpose"] = str(candidate.get("purpose", ""))[:1_000]
        candidate["est_lines"] = _positive_int(
            candidate.get("est_lines"), 50)
        candidate["est_bytes"] = estimated_item_bytes(candidate)
        workstream = str(candidate.get("workstream", "")).strip()
        candidate["workstream"] = (
            workstream[:100] if workstream
            else (path.split("/", 1)[0] if "/" in path else "root")
        )
        candidate["depends_on"] = _normalized_dependencies(
            candidate, root, safe_relpath)
        plan.append(candidate)

    planned_paths = {item["path"] for item in plan}
    unknown_dependencies = tuple(
        {"path": item["path"], "dependency": dependency}
        for item in plan
        for dependency in item.get("depends_on", [])
        if dependency not in planned_paths
    )
    ordered, cycles = _stable_topological_order(plan)
    oversized = tuple(
        item for item in ordered
        if item["est_bytes"] > target_file_bytes
    )
    hard_oversized = tuple(
        item for item in ordered
        if item["est_bytes"] > max_file_bytes
    )
    invalid_paths = {
        item["path"] for item in oversized
    } | {
        item["path"] for item in unknown_dependencies
    } | set(cycles)
    blocked_dependencies = []
    changed = True
    while changed:
        changed = False
        for item in ordered:
            if item["path"] in invalid_paths:
                continue
            blocked = tuple(
                dependency for dependency in item.get("depends_on", [])
                if dependency in invalid_paths)
            if blocked:
                invalid_paths.add(item["path"])
                blocked_dependencies.append({
                    "path": item["path"],
                    "dependencies": blocked,
                })
                changed = True
    runnable = tuple(
        item for item in ordered if item["path"] not in invalid_paths)
    hard_paths = {item["path"] for item in hard_oversized}
    failures = [
        *rejected,
        *({"path": str(item.get("path", ""))
                     if isinstance(item, dict) else "",
           "reason": f"omitted by --max-files ({max_files})"}
          for item in omitted),
        *([{
            "path": "",
            "reason": (
                f"{omitted_count - len(omitted)} additional planned file(s) "
                f"omitted by --max-files ({max_files})"
            ),
        }] if omitted_count > len(omitted) else []),
        *({"path": item["path"],
           "reason": (
               f"estimated {item['est_bytes']:,} bytes exceeds the safe "
               f"per-artifact target ({target_file_bytes:,}); decompose it"
           )}
          for item in oversized if item["path"] not in hard_paths),
        *({"path": item["path"],
           "reason": (
               f"estimated {item['est_bytes']:,} bytes exceeds "
               f"--max-file-bytes ({max_file_bytes:,})"
           )}
          for item in hard_oversized),
        *({"path": item["path"],
           "reason": f"unknown dependency: {item['dependency']}"}
          for item in unknown_dependencies),
        *({"path": path, "reason": "dependency cycle"}
          for path in cycles),
        *({"path": item["path"],
           "reason": (
               "depends on unavailable planned artifact(s): "
               + ", ".join(item["dependencies"])
           )}
          for item in blocked_dependencies),
    ]
    return {
        "plan": ordered,
        "runnable_plan": runnable,
        "rejected": tuple(rejected),
        "omitted": omitted,
        "omitted_count": omitted_count,
        "duplicate_paths": tuple(dict.fromkeys(duplicates)),
        "unknown_dependencies": unknown_dependencies,
        "cycles": cycles,
        "blocked_dependencies": tuple(blocked_dependencies),
        "oversized": oversized,
        "hard_oversized": hard_oversized,
        "failures": tuple(failures),
        "complete": not failures,
    }


def _read_json_object(text, start):
    """Return ``(end, object)`` for one complete top-level object or ``None``."""
    depth, end, in_string, escaped = 0, start, False, False
    while end < len(text):
        character = text[end]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                end += 1
                break
        end += 1
    if depth != 0:
        return None
    try:
        candidate = json.loads(text[start:end])
        return end, candidate if isinstance(candidate, dict) else None
    except (ValueError, RecursionError):
        return end, None


def _scan_file_objects(text):
    """Read only line-started complete JSON objects from a generation reply."""
    objects, index, line_start = [], 0, 0
    while index < len(text):
        if text[index] == "\n":
            index, line_start = index + 1, index + 1
        elif text[index] != "{":
            index += 1
        elif text[line_start:index].strip():
            newline = text.find("\n", index)
            index = newline + 1 if newline >= 0 else len(text)
            line_start = index
        else:
            scanned = _read_json_object(text, index)
            if scanned is None:
                break
            index, candidate = scanned
            if candidate is not None:
                objects.append(candidate)
                while index < len(text) and text[index] in " \t\r,":
                    index += 1
                if index < len(text) and text[index] == "{":
                    line_start = index
                    continue
            newline = text.find("\n", index)
            index = newline + 1 if newline >= 0 else len(text)
            line_start = index
    return objects


def parse_file_records(text):
    """Recover complete top-level JSON file records without repairing a cut tail."""
    files = []
    for candidate in _scan_file_objects(text):
        if "path" in candidate or "content" in candidate:
            files.append(candidate)
        elif isinstance(candidate.get("files"), list):
            files.extend(item for item in candidate["files"] if isinstance(item, dict))
    return files


def classify_file_records(records, *, wanted_paths, plan_paths, done_paths,
                          root, max_file_bytes, salvaged_partial,
                          safe_relpath):
    """Classify untrusted generated files without mutating workflow state."""
    wanted, planned, done = set(wanted_paths), set(plan_paths), set(done_paths)
    accepted, failures, dropped, seen = [], [], [], set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(
                record.get("content"), str):
            continue
        raw_path = str(record.get("path", ""))
        try:
            relative = safe_relpath(raw_path, root)
        except ValueError as error:
            failures.append((raw_path, f"unsafe path: {error}"))
            continue
        if relative not in planned:
            if relative not in dropped:
                dropped.append(relative)
            continue
        if relative not in wanted or relative in done or relative in seen:
            continue
        try:
            content_bytes = record["content"].encode("utf-8")
        except UnicodeEncodeError:
            continue
        if len(content_bytes) > max_file_bytes:
            failures.append(
                (relative, f"file exceeds --max-file-bytes ({max_file_bytes:,})"))
            continue
        accepted.append((relative, record["content"]))
        seen.add(relative)
    if accepted and salvaged_partial:
        accepted.pop()
    return tuple(accepted), tuple(failures), tuple(dropped)


def _positive_int(value, default):
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def generation_batches(plan, *, done_paths, max_tokens, chars_per_token,
                       target_batch_bytes=None, max_batch_files=None):
    """Return immutable model-budgeted generation batches and a call ceiling."""
    done = set(done_paths)
    todo = tuple(dict(item) for item in plan if item["path"] not in done)
    estimates = {item["path"]: estimated_item_bytes(item) for item in todo}
    budget = (
        _positive_int(target_batch_bytes, 0)
        or max(4000, int(max_tokens * chars_per_token * 0.35))
    )
    batches, current, current_size = [], [], 0
    for item in todo:
        estimate = estimates[item["path"]]
        current_workstream = (
            current[0].get("workstream") if current else None)
        item_workstream = item.get("workstream")
        if current and (
                current_size + estimate > budget
                or current_workstream != item_workstream
                or (
                    _positive_int(max_batch_files, 0)
                    and len(current) >= max_batch_files)):
            batches.append(tuple(current))
            current, current_size = [], 0
        current.append(item)
        current_size += estimate
    if current:
        batches.append(tuple(current))
    return tuple(batches), len(batches) + 2 * len(todo)


def ready_generation_wave(batches, *, done_paths, failed_paths, width):
    """Select an immutable, dependency-ready, workstream-distinct batch wave."""
    done, failed = set(done_paths), set(failed_paths)
    limit = max(1, _positive_int(width, 1))
    # The first generated batch establishes a concrete coherence anchor even
    # when a model omitted explicit dependencies from an otherwise valid plan.
    if not done:
        limit = 1
    ready, pending, blocked = [], [], []
    active_workstreams = set()
    for original in batches:
        batch = tuple(dict(item) for item in original)
        blocked_paths = {
            item["path"] for item in batch
            if set(item.get("depends_on", ())) & failed
        }
        changed = True
        while changed:
            expanded = {
                item["path"] for item in batch
                if set(item.get("depends_on", ())) & blocked_paths
            }
            next_blocked = blocked_paths | expanded
            changed = next_blocked != blocked_paths
            blocked_paths = next_blocked
        for item in batch:
            if item["path"] not in blocked_paths:
                continue
            dependencies = sorted(
                set(item.get("depends_on", ())) & (failed | blocked_paths))
            blocked.append((
                item["path"],
                "dependency generation failed: "
                + ", ".join(dependencies),
            ))
        candidate = tuple(
            item for item in batch if item["path"] not in blocked_paths)
        if not candidate:
            continue
        candidate_paths = {item["path"] for item in candidate}
        external_dependencies = {
            dependency
            for item in candidate
            for dependency in item.get("depends_on", ())
            if dependency not in candidate_paths
        }
        workstream = str(candidate[0].get("workstream", ""))
        can_run = (
            external_dependencies <= done
            and len(ready) < limit
            and workstream not in active_workstreams
        )
        if can_run:
            ready.append(candidate)
            active_workstreams.add(workstream)
        else:
            pending.append(candidate)
    return tuple(ready), tuple(pending), tuple(blocked)


def _generation_user(task, batch, overview, already, context, compact,
                     project_brief):
    targets = "\n".join(
        f"  {item['path']} — {item.get('purpose', '')}" for item in batch)
    brief = (
        f"\n\nSHARED PROJECT BRIEF (preserve exactly across every file):\n"
        f"{project_brief}"
        if project_brief else ""
    )
    if compact:
        prompt = (f"TASK: {task}\n\nRECOVERY GENERATION: a prior attempt "
                  "spent its output on reasoning before emitting these files. "
                  "Generate ONLY these complete file records now:\n"
                  f"{targets}{brief}")
    else:
        prompt = (f"TASK: {task}\n\nFULL PLAN (for cross-file consistency):\n"
                  f"{overview}\n\nALREADY GENERATED (do not repeat):\n"
                  f"{already}\n\nGENERATE NOW — complete content for exactly "
                  f"these files:\n{targets}{brief}")
    return prompt + (f"\n\nContext:\n{context}" if context else "")


def generation_prompt(*, task, batch, plan, done_paths, context, system_chars,
                      single_shot_chars, recovery_paths, project_brief=""):
    """Fit one build request by progressively compacting shared context."""
    overview = "\n".join(
        f"  {item['path']} — {item.get('purpose', '')}" for item in plan)
    already = "\n".join(f"  {path}" for path in done_paths) or "  (none yet)"
    compact = any(item["path"] in set(recovery_paths) for item in batch)
    brief = str(project_brief or "")[:6_000]
    head = system_chars + 2000
    prompt = _generation_user(
        task, batch, overview, already, context, compact, brief)
    if head + len(prompt) > single_shot_chars:
        prompt = _generation_user(
            task, batch, overview, "  (omitted to fit the window)", context,
            compact, brief)
    if head + len(prompt) > single_shot_chars and context:
        empty = _generation_user(
            task, batch, overview, "  (omitted)", "", compact, brief)
        room = max(0, single_shot_chars - head - len(empty)
                   - len("\n\nContext:\n"))
        prompt = _generation_user(
            task, batch, overview, "  (omitted)", context[:room], compact,
            brief)
    if head + len(prompt) > single_shot_chars:
        paths_only = "\n".join(f"  {item['path']}" for item in plan)
        prompt = _generation_user(
            task, batch, paths_only, "  (omitted)", "", compact, brief[:2_000])
    return prompt if head + len(prompt) <= single_shot_chars else None


def generation_recovery(batch, *, done_paths, failed_paths, response_meta,
                        attempts, recovery_paths):
    """Return the immutable retry transition for files missing from one reply."""
    done, failed = set(done_paths), set(failed_paths)
    missing = tuple(dict(item) for item in batch
                    if item["path"] not in done and item["path"] not in failed)
    next_attempts = dict(attempts)
    next_recovery = list(dict.fromkeys(recovery_paths))
    failures = []
    output_starved = isinstance(response_meta, dict) and bool(
        response_meta.get("reasoning_draft")
        or response_meta.get("finish_reason") == "length"
        or response_meta.get("salvaged_partial"))
    if output_starved:
        for item in missing:
            if item["path"] not in next_recovery:
                next_recovery.append(item["path"])
    retries = ()
    if missing and len(batch) > 1:
        retries = tuple((item,) for item in missing)
    elif missing:
        path = missing[0]["path"]
        next_attempts[path] = next_attempts.get(path, 0) + 1
        if next_attempts[path] < 3:
            retries = (missing,)
        else:
            failures.append((
                path,
                "the model didn't return this file after 3 attempts — it may "
                "be too large for one response, or the model kept proposing a "
                "different/rejected path; simplify or split it",
            ))
    return (retries, tuple(next_attempts.items()), tuple(next_recovery),
            tuple(failures))


__all__ = ("state_path", "safe_relative_path", "resume_identity",
           "normalize_resume_state", "validate_plan_items", "parse_file_records",
           "estimated_item_bytes", "artifact_target_bytes",
           "generation_output_tokens", "prioritize_plan_formats",
           "assess_plan_contract", "classify_file_records",
           "generation_batches", "ready_generation_wave",
           "generation_prompt", "generation_recovery")
