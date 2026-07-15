"""Dependency-injected preparation primitives shared by audit workflows."""

import dataclasses
import json
import re


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_SPLIT_ARTIFACT = re.compile(
    r"(?i)\b(?:suspected\s+)?split artifact\b"
    r"|\bfile (?:being )?split across chunks\b")
_CROSS_FILE_HINT = re.compile(
    r"cross-file|another chunk|another file|defined (?:in|elsewhere)|"
    r"needs? cross-file confirmation", re.I)


def _finding_signature(finding):
    path = str(finding.get("file", "")).strip().replace("\\", "/").lstrip("./")
    try:
        line = int(finding.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    words = re.sub(r"\W+", " ", str(finding.get("title", ""))).lower().split()
    return path, line, tuple(words[:4])


def _titles_match(first, second):
    length = min(len(first), len(second), 4)
    return (not first and not second) if length == 0 else first[:length] == second[:length]


def _is_split_artifact(finding):
    if not isinstance(finding, dict):
        return False
    text = " ".join(str(finding.get(key, ""))
                    for key in ("title", "defect", "scenario", "fix"))
    return bool(_SPLIT_ARTIFACT.search(text))


def dedupe_findings(findings):
    """Conservatively merge duplicate chunk findings without losing context."""
    kept = []
    for finding in findings:
        if not isinstance(finding, dict) or _is_split_artifact(finding):
            continue
        path, line, title = _finding_signature(finding)
        slot = next((entry for entry in kept
                     if entry[0][0] == path and _titles_match(entry[0][2], title)
                     and abs(entry[0][1] - line) <= 3), None)
        if slot is None:
            kept.append(((path, line, title), finding))
            continue
        previous = slot[1]
        best = (finding if SEVERITY_ORDER.get(finding.get("severity"), 9)
                < SEVERITY_ORDER.get(previous.get("severity"), 9) else previous)
        richest = max(previous, finding,
                      key=lambda value: len(str(value.get("scenario", ""))))
        if len(str(richest.get("scenario", ""))) > len(str(best.get("scenario", ""))):
            best = {**best, "scenario": richest.get("scenario", "")}
        kept[kept.index(slot)] = (slot[0], best)
    return sorted((finding for _, finding in kept),
                  key=lambda finding: SEVERITY_ORDER.get(finding.get("severity"), 9))


def verdict_from(findings, partial):
    """Derive a conservative audit verdict from structured findings."""
    severities = {finding.get("severity") for finding in findings
                  if isinstance(finding, dict)}
    if partial:
        return "NEEDS WORK"
    if {"CRITICAL", "HIGH"} & severities:
        return "FIX FIRST"
    return "NEEDS WORK" if {"MEDIUM", "LOW"} & severities else "SHIP"


def extract_json(text):
    """Best-effort object extraction that marks only safe truncation repairs."""
    if not text:
        return None
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    if fence:
        try:
            parsed = json.loads(fence.group(1).strip())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    first = stripped.find("{")
    if first == -1:
        return None
    fragment = stripped[first:]
    try:
        parsed, _end = decoder.raw_decode(fragment)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    if fragment.count('"') % 2 == 0:
        missing_brackets = fragment.count("[") - fragment.count("]")
        missing_braces = fragment.count("{") - fragment.count("}")
        if 0 <= missing_brackets <= 5 and 1 <= missing_braces <= 5:
            try:
                parsed = json.loads(
                    fragment + "]" * missing_brackets + "}" * missing_braces)
                if isinstance(parsed, dict):
                    return {**parsed, "_repaired": True}
            except json.JSONDecodeError:
                pass
    index = first + 1
    while True:
        start = stripped.find("{", index)
        if start == -1:
            return None
        try:
            parsed, _end = decoder.raw_decode(stripped[start:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        index = start + 1


def prepare_sample(model, catalog, labeled, system_prompt, args, *,
                   model_profile, request_spec, response_format,
                   findings_schema, json_instruction):
    """Build the model-specific request and prompt for one audit sample."""
    profile = model_profile(catalog, model)
    single, chunk = profile.single_shot_chars, profile.chunk_chars
    total = sum(len(text) for _, text in labeled)
    spec = request_spec.from_args(args)
    structured = response_format(model, profile, findings_schema)
    prepared = dataclasses.replace(
        spec.with_output_budget(profile, total if total <= single else chunk),
        response_format=structured,
    )
    prompt = system_prompt + (
        json_instruction
        if structured is None or structured.get("type") == "json_object" else ""
    )
    return prepared, prompt, single, chunk, total


def single_shot_key(model, system_prompt, labeled, spec, *, files_block,
                    cache_key):
    """Build the stable, salt-aware cache key for one audit completion."""
    return cache_key(
        model, system_prompt, files_block(labeled), spec.max_tokens,
        spec.temperature, spec.response_format, salt=spec._cache_salt,
    )


def reduce_findings(texts, *, parse, dedupe, verdict):
    """Reduce parsed chunk findings without masking incomplete coverage."""
    collected = []
    unparsed = 0
    repaired = 0
    for text in texts:
        parsed = parse(text)
        if parsed and isinstance(parsed.get("findings"), list):
            collected.extend(parsed["findings"])
            repaired += 1 if parsed.get("_repaired") else 0
        else:
            unparsed += 1
    findings = dedupe(collected)
    final_verdict = ("NEEDS WORK" if (unparsed or repaired)
                     else verdict(findings, False))
    return {
        "findings": findings,
        "verdict": final_verdict,
        "_unparsed_chunks": unparsed,
        "_repaired_chunks": repaired,
    }


def cross_file_suspects(final_text, paths, cap=6):
    """Return bounded, first-mentioned paths needing cross-file confirmation."""
    obj = extract_json(final_text)
    if obj is not None and isinstance(obj.get("findings"), list):
        entries = [" ".join(str(value) for value in finding.values())
                   for finding in obj["findings"] if isinstance(finding, dict)]
    else:
        entries = [line for line in (final_text or "").splitlines() if line.strip()]
    suspects = []
    for entry in entries:
        named = [path for path in paths if path and path in entry]
        if len(named) >= 2 or (named and _CROSS_FILE_HINT.search(entry)):
            suspects.extend(path for path in named if path not in suspects)
        if len(suspects) >= cap:
            break
    return suspects[:cap]


def parse_audit_object(raw, *, parse_prose, has_unparsed):
    """Prefer structured findings while preserving prose recovery safeguards."""
    obj = extract_json(raw) if isinstance(raw, str) else raw
    if isinstance(raw, str) and obj and isinstance(obj.get("findings"), list):
        if not obj["findings"]:
            recovered = parse_prose(raw)
            if recovered and recovered.get("findings"):
                return recovered
            if has_unparsed(raw):
                return None
        return obj
    if obj and isinstance(obj.get("findings"), list):
        return obj
    return parse_prose(raw) if isinstance(raw, str) else None


def select_cross_file_inputs(suspects, by_path, cap):
    """Select ordered suspect files without exceeding the confirmation budget."""
    picked, used = [], 0
    for path in suspects:
        text = by_path.get(path)
        if not text:
            continue
        room = cap - used
        if room <= 500:
            break
        segment = text if len(text) <= room else (
            text[:room] + "\n[ambient: file truncated for the bounded cross-file pass]")
        picked.append((path, segment))
        used += len(segment)
    return picked, used


def merge_cross_file_findings(first, second, second_incomplete, *, dedupe,
                              verdict, as_pos_int):
    """Merge confirmation findings without laundering incomplete coverage."""
    merged = dedupe(list(first.get("findings") or []) + list(second.get("findings") or []))
    unparsed = as_pos_int(first.get("_unparsed_chunks"), 0)
    repaired = as_pos_int(first.get("_repaired_chunks"), 0)
    if second_incomplete:
        repaired += as_pos_int(second.get("_repaired_chunks"), 1)
    return {"findings": merged,
            "verdict": "NEEDS WORK" if (unparsed or repaired) else verdict(merged, False),
            "_unparsed_chunks": unparsed, "_repaired_chunks": repaired}


def normalize_findings(findings):
    """Copy findings while splitting a leaked ``file:line`` location field."""
    normalized = []
    for finding in findings:
        if not isinstance(finding, dict):
            normalized.append(finding)
            continue
        copied = dict(finding)
        match = re.match(r"^(.*?):(\d+)$", str(copied.get("file", "")))
        if match:
            copied["file"] = match.group(1)
            copied.setdefault("line", int(match.group(2)))
        normalized.append(copied)
    return normalized


def effective_verdict(stated, findings, *, partial, verdict):
    """Prevent model prose from reporting a clean verdict over incomplete work."""
    resolved = stated or verdict(findings, partial)
    if partial and resolved == "SHIP":
        return "NEEDS WORK"
    if resolved == "SHIP" and findings:
        return ("FIX FIRST" if any(isinstance(finding, dict)
                and finding.get("severity") in ("CRITICAL", "HIGH")
                for finding in findings) else "NEEDS WORK")
    return resolved


__all__ = ("extract_json", "dedupe_findings", "verdict_from", "prepare_sample",
           "single_shot_key", "reduce_findings", "cross_file_suspects", "parse_audit_object",
           "select_cross_file_inputs", "merge_cross_file_findings", "normalize_findings",
           "effective_verdict")
