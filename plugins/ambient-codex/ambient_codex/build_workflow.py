"""Bounded path, identity, and state policies for resumable build workflows."""

import hashlib
import json
import os
import re


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
    payload = {"identity_version": 2, "runtime_version": runtime_version,
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
    failed = state.get("failed")
    return dict(state, plan=plan, done=done,
                failed=[dict(item) for item in failed
                        if isinstance(item, dict) and isinstance(item.get("path"), str)
                        and isinstance(item.get("reason"), str)]
                if isinstance(failed, list) else [])


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
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def generation_batches(plan, *, done_paths, max_tokens, chars_per_token):
    """Return immutable model-budgeted generation batches and a call ceiling."""
    done = set(done_paths)
    todo = tuple(dict(item) for item in plan if item["path"] not in done)
    estimates = {
        item["path"]: max(200, _positive_int(item.get("est_lines"), 50) * 40)
        for item in todo
    }
    budget = max(4000, int(max_tokens * chars_per_token * 0.35))
    batches, current, current_size = [], [], 0
    for item in todo:
        estimate = estimates[item["path"]]
        if current and current_size + estimate > budget:
            batches.append(tuple(current))
            current, current_size = [], 0
        current.append(item)
        current_size += estimate
    if current:
        batches.append(tuple(current))
    return tuple(batches), 3 * max(1, len(todo)) + 4


def _generation_user(task, batch, overview, already, context, compact):
    targets = "\n".join(
        f"  {item['path']} — {item.get('purpose', '')}" for item in batch)
    if compact:
        prompt = (f"TASK: {task}\n\nRECOVERY GENERATION: a prior attempt "
                  "spent its output on reasoning before emitting these files. "
                  "Generate ONLY these complete file records now:\n"
                  f"{targets}")
    else:
        prompt = (f"TASK: {task}\n\nFULL PLAN (for cross-file consistency):\n"
                  f"{overview}\n\nALREADY GENERATED (do not repeat):\n"
                  f"{already}\n\nGENERATE NOW — complete content for exactly "
                  f"these files:\n{targets}")
    return prompt + (f"\n\nContext:\n{context}" if context else "")


def generation_prompt(*, task, batch, plan, done_paths, context, system_chars,
                      single_shot_chars, recovery_paths):
    """Fit one build request by progressively compacting shared context."""
    overview = "\n".join(
        f"  {item['path']} — {item.get('purpose', '')}" for item in plan)
    already = "\n".join(f"  {path}" for path in done_paths) or "  (none yet)"
    compact = any(item["path"] in set(recovery_paths) for item in batch)
    head = system_chars + 2000
    prompt = _generation_user(task, batch, overview, already, context, compact)
    if head + len(prompt) > single_shot_chars:
        prompt = _generation_user(
            task, batch, overview, "  (omitted to fit the window)", context,
            compact)
    if head + len(prompt) > single_shot_chars and context:
        empty = _generation_user(task, batch, overview, "  (omitted)", "", compact)
        room = max(0, single_shot_chars - head - len(empty)
                   - len("\n\nContext:\n"))
        prompt = _generation_user(
            task, batch, overview, "  (omitted)", context[:room], compact)
    if head + len(prompt) > single_shot_chars:
        paths_only = "\n".join(f"  {item['path']}" for item in plan)
        prompt = _generation_user(
            task, batch, paths_only, "  (omitted)", "", compact)
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
        midpoint = max(1, len(missing) // 2)
        retries = tuple(part for part in (missing[:midpoint], missing[midpoint:])
                        if part)
    elif missing:
        path = missing[0]["path"]
        next_attempts[path] = next_attempts.get(path, 0) + 1
        if next_attempts[path] < 2:
            retries = (missing,)
        else:
            failures.append((
                path,
                "the model didn't return this file after 2 attempts — it may "
                "be too large for one response, or the model kept proposing a "
                "different/rejected path; simplify or split it",
            ))
    return (retries, tuple(next_attempts.items()), tuple(next_recovery),
            tuple(failures))


__all__ = ("state_path", "safe_relative_path", "resume_identity",
           "normalize_resume_state", "validate_plan_items", "parse_file_records",
           "classify_file_records", "generation_batches", "generation_prompt",
           "generation_recovery")
