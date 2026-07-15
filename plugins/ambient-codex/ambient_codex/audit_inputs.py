"""Audit parsing, rendering, repository intake, and cross-file confirmation."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AuditInputDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def extract_json(text, deps=None):
    """Facade seam for the extracted tolerant audit JSON parser."""
    _audit_core = deps._audit_core
    return _audit_core.extract_json(text)


def _finding_sig(finding, deps=None):
    """Compatibility seam for consensus finding identity."""
    _audit_core = deps._audit_core
    return _audit_core._finding_signature(finding)


def _titles_match(first, second, deps=None):
    """Compatibility seam for consensus duplicate matching."""
    _audit_core = deps._audit_core
    return _audit_core._titles_match(first, second)


def parse_audit_object(raw, deps=None):
    """Parse one model audit reply into a findings object.

    Strict JSON is preferred, but map-reduce workers can receive the same
    GLM-style prose that render_findings already recovers in the single-shot
    path. A finding-shaped but unrecoverable reply returns None so coverage
    remains partial instead of becoming a clean pass.
    """
    _audit_core = deps._audit_core
    _text_has_unparsed_finding = deps._text_has_unparsed_finding
    parse_prose_findings = deps.parse_prose_findings
    return _audit_core.parse_audit_object(
        raw, parse_prose=parse_prose_findings,
        has_unparsed=_text_has_unparsed_finding)


def dedupe_findings(findings, deps=None):
    """Facade seam for extracted deterministic finding consolidation."""
    _audit_core = deps._audit_core
    return _audit_core.dedupe_findings(findings)


def _verdict_from(findings, partial, deps=None):
    """Facade seam for extracted conservative audit verdict derivation."""
    _audit_core = deps._audit_core
    return _audit_core.verdict_from(findings, partial)


def findings_reducer(texts, deps=None):
    """Structured reduce (A5): parse each chunk's JSON findings, dedupe in
    Python, recompute the verdict — no re-billed LLM synthesis call. A chunk
    whose output won't parse is a COVERAGE GAP, not a clean pass:
    it forces a non-SHIP verdict rather than silently dropping to
    {"findings":[], "SHIP"}."""
    _audit_core = deps._audit_core
    _verdict_from = deps._verdict_from
    dedupe_findings = deps.dedupe_findings
    json = deps.json
    parse_audit_object = deps.parse_audit_object
    return json.dumps(_audit_core.reduce_findings(
        texts,
        parse=parse_audit_object,
        dedupe=dedupe_findings,
        verdict=_verdict_from,
    ))


def _text_has_unparsed_finding(text, deps=None):
    """Facade seam for the extracted bounded prose parser."""
    _audit_prose = deps._audit_prose
    return _audit_prose.text_has_unparsed_finding(text)


def parse_prose_findings(text, deps=None):
    """Facade seam for prose finding recovery."""
    _audit_prose = deps._audit_prose
    _verdict_from = deps._verdict_from
    return _audit_prose.parse_prose_findings(text, verdict=_verdict_from)


def render_findings(raw, fmt, api_key, partial=False, reason="", model=None, deps=None):
    """Render structured audit findings as JSON or a clean report. Degrades to
    printing the raw text if it can't be parsed (never drops the output).
    Returns the EFFECTIVE partial flag — render_findings can discover coverage
    gaps its caller didn't know about (unparseable/repaired chunks), and the
    caller's exit code must reflect them. Also LEARNS: whether the model
    honored structured output (clean JSON) or needed the prose fallback is
    recorded per model so the next call starts from the working path."""
    _audit_core = deps._audit_core
    _text_has_unparsed_finding = deps._text_has_unparsed_finding
    _verdict_from = deps._verdict_from
    emit_json = deps.emit_json
    extract_json = deps.extract_json
    json = deps.json
    paint = deps.paint
    parse_prose_findings = deps.parse_prose_findings
    record_cap = deps.record_cap
    redact = deps.redact
    obj = extract_json(raw) if isinstance(raw, str) else raw
    recovered_from_prose = False
    # A model may emit an EMPTY '{"findings":[],"verdict":"SHIP"}' JSON AND then
    # real PROSE findings (Codex round 29). Don't trust an empty JSON result if
    # the surrounding text carries prose findings — recover them instead of
    # faking a clean pass.
    if (isinstance(raw, str) and obj and isinstance(obj.get("findings"), list)
            and not obj["findings"]):
        recovered = parse_prose_findings(raw)
        if recovered and recovered.get("findings"):
            obj, recovered_from_prose = recovered, True
        elif _text_has_unparsed_finding(raw):
            obj = None  # empty JSON but the text reports a defect → don't fake clean
    if not obj or not isinstance(obj.get("findings"), list):
        # The model ignored the JSON schema. Before giving up, recover findings
        # from its PROSE (the tool's own format) — the adaptive fallback that
        # makes structured audits work on any reasoning model.
        recovered = parse_prose_findings(raw) if isinstance(raw, str) else None
        if recovered is not None:
            obj = recovered
            recovered_from_prose = True
        else:
            if isinstance(raw, str):
                record_cap(model, "structured_json", False)
            text = raw if isinstance(raw, str) else json.dumps(raw)
            if fmt == "json":
                # --json must ALWAYS emit valid JSON — a consumer can't parse
                # raw model text. Wrap it in an envelope with a non-clean
                # verdict and the raw text for the human.
                emit_json("audit", model=model, api_key=api_key,
                          findings=[], verdict="NEEDS WORK", partial=True,
                          reason="model output was not valid parseable JSON",
                          extra={"coverage_complete": False,
                                 "raw": redact(text, api_key)[:4000]},
                          exit_now=False)
            else:
                print(redact(text, api_key))
            return True
    # A direct SINGLE-SHOT model response tells us whether this model honors
    # structured output: clean JSON => yes; prose recovery => no. The reducer
    # emits its own JSON STRING carrying _unparsed_chunks / _repaired_chunks
    # markers — that is deterministic tool output, NOT this model's structured
    # reply (Codex: it was training structured_json=True from partial reducer
    # output), so skip recording for it.
    is_reducer_output = isinstance(obj, dict) and any(
        k in obj for k in ("_unparsed_chunks", "_repaired_chunks", "_repaired"))
    if isinstance(raw, str) and not is_reducer_output:
        record_cap(model, "structured_json", not recovered_from_prose)
    findings = _audit_core.normalize_findings(obj["findings"])
    # Unparseable/repaired chunks (from the reducer) are coverage gaps → partial.
    if obj.get("_unparsed_chunks"):
        partial = True
        reason = (reason + "; " if reason else "") + \
            f"{obj['_unparsed_chunks']} chunk(s) returned unparseable output"
    if obj.get("_repaired_chunks") or obj.get("_repaired"):
        partial = True
        reason = (reason + "; " if reason else "") + \
            ("model output was truncated mid-findings and repaired — trailing "
             "findings may be missing")
    verdict = _audit_core.effective_verdict(
        obj.get("verdict"), findings, partial=partial, verdict=_verdict_from)
    if fmt == "json":
        extra = {"coverage_complete": not partial}
        if recovered_from_prose:
            # Transparent: the findings are real but were recovered from prose
            # because the model ignored the JSON schema. NOT a coverage gap.
            extra["recovered_from_prose"] = True
        emit_json("audit", model=model, api_key=api_key,
                  findings=findings, verdict=verdict, partial=partial,
                  reason=reason or None, extra=extra, exit_now=False)
        return partial
    # report format
    if partial:
        print(paint(f"⚠ PARTIAL COVERAGE — {reason}", "1;33") + "\n")
    if not findings:
        print("No defects found.")
    SEV_COLOR = {"CRITICAL": "1;31", "HIGH": "31", "MEDIUM": "33", "LOW": "2"}
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "?"))
        head = (f"[{paint(sev, SEV_COLOR.get(sev, '0'))}/{f.get('confidence', '?')}] "
                f"{f.get('file', '?')}:{f.get('line', '?')} — {f.get('title', '')}")
        print(redact(head, api_key))
        if f.get("scenario"):
            print(redact(f"    {f['scenario']}", api_key))
        if f.get("fix"):
            print(redact(f"    fix: {f['fix']}", api_key))
        print()
    # The verdict string is MODEL OUTPUT — normalize it to the closed set so a
    # poisoned reply can't smuggle terminal escapes or fake banner text through
    # the one line users trust most.
    if verdict not in ("SHIP", "FIX FIRST", "NEEDS WORK"):
        verdict = _verdict_from(findings, partial)
    print(redact(f"Verdict: {verdict}", api_key))
    return partial


def with_line_gutters(labeled, deps=None):
    """Prefix each line with its absolute 1-based number ('  42| code') so a
    reviewer can cite EXACT lines even after a file is split across chunks —
    without this the model reports chunk-relative numbers that are silently
    wrong on any map-reduced audit (A2)."""
    _repository_core = deps._repository_core
    return list(_repository_core.with_line_gutters(tuple(labeled)))


def _repo_candidate_paths(root, deps=None):
    """(relative paths, used_git). Inside a git repo `git ls-files` (tracked +
    untracked-not-ignored) enumerates candidates so .gitignore is respected;
    anywhere else a plain scanner PRUNES vendored/dot directories and never
    descends through symlinked directories."""
    REPO_SKIP_DIRS = deps.REPO_SKIP_DIRS
    _repository_core = deps._repository_core
    subprocess = deps.subprocess
    paths, used_git = _repository_core.candidate_paths(
        root,
        subprocess.run,
        subprocess.TimeoutExpired,
        REPO_SKIP_DIRS,
        popen=getattr(subprocess, "Popen", None),
    )
    return list(paths), used_git


def repo_walk(root, per_file_cap=None, deps=None):
    """Enumerate a repo's auditable TEXT source files (5b). Returns
    (files [(rel, abs, size)], skipped counters, used_git). Bounded and safe
    by construction: candidate paths that are absolute or contain '..' are
    rejected VERBATIM (git output is data, not trust); symlinks are skipped
    entirely (lstat + S_ISREG), so nothing can escape `root`; vendored dirs,
    lockfiles, empty files, files above the bounded input ceiling, and
    NUL-sniffed binaries are skipped with counts. Oversized source paths are
    retained in `skipped["oversize_paths"]` so the caller can force an
    explicit coverage gap instead of allowing a clean verdict over unread
    source."""
    REPO_FILE_MAX_BYTES = deps.REPO_FILE_MAX_BYTES
    REPO_LOCKFILES = deps.REPO_LOCKFILES
    REPO_SKIP_DIRS = deps.REPO_SKIP_DIRS
    _repo_candidate_paths = deps._repo_candidate_paths
    _repository_core = deps._repository_core
    if per_file_cap is None:
        per_file_cap = REPO_FILE_MAX_BYTES
    rels, used_git = _repo_candidate_paths(root)
    files, skipped = _repository_core.classify_repository_files(
        root,
        tuple(rels),
        used_git,
        per_file_cap,
        REPO_SKIP_DIRS,
        REPO_LOCKFILES,
    )
    public_skipped = {
        "binary": skipped.binary,
        "lockfile": skipped.lockfile,
        "oversize": skipped.oversize,
        "oversize_paths": list(skipped.oversize_paths),
        "nonregular": skipped.nonregular,
        "vendored": skipped.vendored,
    }
    return list(files), public_skipped, used_git


def _guttered_size(full, size, deps=None):
    """Post-gutter char estimate for one repo file BEFORE it is read as
    text: with_line_gutters prefixes every line with '<n>| ' (width = digits
    of the line count, min 2), inflating line-dense sources 15-20%+ — the
    input ceiling must bound what is actually SENT, not the raw bytes
. Newlines counted in binary: exact for ASCII, conservative
    (over-counts chars) for multibyte — never under the true sent size by
    more than the decode difference."""
    _repository_core = deps._repository_core
    return _repository_core.guttered_file_size(full, size)


def repo_audit_inputs(args, api_key, deps=None):
    """Build audit inputs for `audit --repo [DIR]` (5b): walk, apply the
    ABS_MAX_CHARS ceiling to the POST-GUTTER size (refuse unless
    --allow-large-input/--allow-partial; then trim to the files that fit and
    surface the rest as an EXPLICIT coverage gap), read + gutter, relabel
    repo-relative. Source files above the bounded per-file ceiling are also
    surfaced as a coverage gap. Returns (labeled, meta)."""
    ABS_MAX_CHARS = deps.ABS_MAX_CHARS
    EXIT_USAGE = deps.EXIT_USAGE
    REPO_FILE_MAX_BYTES = deps.REPO_FILE_MAX_BYTES
    _fail_exit = deps._fail_exit
    _guttered_size = deps._guttered_size
    os = deps.os
    read_files = deps.read_files
    repo_walk = deps.repo_walk
    sys = deps.sys
    with_line_gutters = deps.with_line_gutters
    root = os.path.realpath(args.repo if args.repo else ".")
    if not os.path.isdir(root):
        _fail_exit(args, "audit", "usage",
                   f"--repo: not a directory: {args.repo}",
                   exit_code=EXIT_USAGE, api_key=api_key)
    files, skipped, used_git = repo_walk(root)
    if not files:
        oversize_hint = ""
        if skipped.get("oversize"):
            oversize_hint = (
                f" {skipped['oversize']} source file(s) exceed the "
                f"{REPO_FILE_MAX_BYTES:,}-byte per-file ceiling."
            )
        _fail_exit(args, "audit", "usage",
                   f"--repo: no auditable text source files found under "
                   f"{root} (binaries, lockfiles, vendored and ignored "
                   f"paths are skipped).{oversize_hint}",
                   exit_code=EXIT_USAGE, api_key=api_key)
    # The ceiling binds the size actually SENT — raw bytes PLUS the line
    # gutters with_line_gutters adds below (15-20%+ on line-dense
    # repos). Counting stops at the first provable overflow on the refuse
    # path, so a huge repo isn't fully re-read just to be refused.
    allow_over = (getattr(args, "allow_cost", False)
                  or getattr(args, "allow_partial", False))
    gutter_sizes, total = [], 0
    for _rel, full, size in files:
        g = _guttered_size(full, size)
        gutter_sizes.append(g)
        total += g
        if total > ABS_MAX_CHARS and not allow_over:
            _fail_exit(
                args, "audit", "cost",
                f"repo totals ~{total:,}+ chars with line-number gutters — "
                f"over the {ABS_MAX_CHARS:,}-char input ceiling. "
                "Audit a subdirectory (--repo DIR/sub), or "
                "pass --allow-large-input or --allow-partial to audit the files "
                "that fit, with the rest reported as an explicit coverage "
                "gap.",
                api_key=api_key)
    omitted = []
    if total > ABS_MAX_CHARS:
        keep, running = [], 0
        budget = ABS_MAX_CHARS - 200_000  # headroom for labels/coverage note
        for item, g in zip(files, gutter_sizes):
            if running + g > budget:
                omitted.append(item[0])
            else:
                keep.append(item)
                running += g
        files, total = keep, running
        print(f"ambient: repo exceeds the input ceiling (post-gutter) — "
              f"auditing {len(files)} file(s) that fit; {len(omitted)} "
              "EXCLUDED (explicit coverage gap): "
              + ", ".join(omitted[:5])
              + ("…" if len(omitted) > 5 else ""), file=sys.stderr)
    rel_of = {full: rel for rel, full, _size in files}
    pairs = read_files([full for _rel, full, _size in files])
    labeled = with_line_gutters([(rel_of.get(p, p), t) for p, t in pairs])
    oversize_paths = skipped.get("oversize_paths", [])
    coverage_notes = []
    if omitted:
        coverage_notes.append(
            f"{len(omitted)} repo file(s) were EXCLUDED from this audit "
            "(input ceiling): " + ", ".join(omitted[:40])
            + ("…" if len(omitted) > 40 else ""))
    if skipped.get("oversize"):
        shown = ", ".join(oversize_paths[:40])
        suffix = "…" if len(oversize_paths) < skipped["oversize"] else ""
        coverage_notes.append(
            f"{skipped['oversize']} source file(s) were EXCLUDED from this "
            f"audit (over {REPO_FILE_MAX_BYTES:,}-byte per-file ceiling)"
            + (f": {shown}{suffix}" if shown else ""))
    if coverage_notes:
        labeled.append((
            "REPO COVERAGE NOTE",
            "; ".join(coverage_notes)
            + ". Coverage is PARTIAL — state this gap and do not issue a "
            "clean verdict."))
    meta = {"root": root, "files": len(files), "chars": total,
            "git": used_git, "skipped": skipped,
            "omitted_over_cap": len(omitted),
            "omitted_oversize": skipped.get("oversize", 0),
            "coverage_gap": bool(coverage_notes)}
    return labeled, meta


def cross_file_suspects(final_text, paths, cap=6, deps=None):
    """Facade seam for extracted cross-file confirmation candidate selection."""
    _audit_core = deps._audit_core
    return _audit_core.cross_file_suspects(final_text, paths, cap)


def run_cross_file_pass(final, labeled, model, profile, args, api_key,
                        api_url, catalog, conf, structured, session=None, deps=None):
    """AT MOST ONE bounded cross-file confirmation call (5c): pass-1 findings
    + the suspect files' content (clipped to half a chunk) go to the SAME
    model in a single gated complete(); its findings are merged in. Failure
    or unparseable output leaves the pass-1 result untouched — the extra
    pass may only ADD information, never lose paid-for work. Pass-1 coverage
    flags (_unparsed/_repaired chunks) are PRESERVED through the merge so a
    partial audit can never launder itself clean."""
    AUDIT_FINDINGS_SCHEMA = deps.AUDIT_FINDINGS_SCHEMA
    AUDIT_JSON_INSTRUCTION = deps.AUDIT_JSON_INSTRUCTION
    AUDIT_SYSTEM_PROMPT = deps.AUDIT_SYSTEM_PROMPT
    ChatError = deps.ChatError
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    _NON_FILE_LABELS = deps._NON_FILE_LABELS
    _as_pos_int = deps._as_pos_int
    _audit_core = deps._audit_core
    _session_or = deps._session_or
    _verdict_from = deps._verdict_from
    adaptive_response_format = deps.adaptive_response_format
    complete = deps.complete
    cross_file_suspects = deps.cross_file_suspects
    dataclasses = deps.dataclasses
    dedupe_findings = deps.dedupe_findings
    extract_json = deps.extract_json
    files_block = deps.files_block
    json = deps.json
    sys = deps.sys
    session = _session_or(session, api_key, api_url, conf)
    api_key, api_url = session.api_key, session.api_url
    spec = RequestSpec.from_args(args)
    known = []
    for label, _text in labeled:
        p = label.split(" [")[0]
        if p not in _NON_FILE_LABELS and p not in known:
            known.append(p)
    suspects = cross_file_suspects(final, known)
    if not suspects:
        return final
    by_path = {}
    for label, text in labeled:
        p = label.split(" [")[0]
        if p in suspects and p not in by_path:
            by_path[p] = text
    cap = max(4_000, min(profile.chunk_chars, profile.single_shot_chars) // 2)
    # The confirmation call is single-shot: summary + suspects must FIT the
    # model's window together, even on a tiny-window model.
    summary_cap = min(20_000, max(2_000, profile.single_shot_chars // 3))
    picked, used = _audit_core.select_cross_file_inputs(suspects, by_path, cap)
    if not picked:
        return final
    summary = final[:summary_cap]
    print(f"ambient: cross-file confirmation — ONE bounded pass over "
          f"{len(picked)} suspect file(s), {used:,} chars (--no-deep skips)",
          file=sys.stderr)
    sp = (AUDIT_SYSTEM_PROMPT
          + "\n\nThis is a bounded CROSS-FILE CONFIRMATION pass: pass-1 "
            "findings below were produced from chunks that could not see "
            "these files together. CONFIRM, refute, or refine the "
            "cross-file claims using the suspect files' real content; "
            "report only findings you can ground in the shown code.")
    deep_spec = spec
    if structured:
        rf = adaptive_response_format(model, profile, AUDIT_FINDINGS_SCHEMA)
        deep_spec = dataclasses.replace(spec, response_format=rf)
        if rf is None or rf.get("type") == "json_object":
            sp += AUDIT_JSON_INSTRUCTION
    user = ("PASS-1 FINDINGS (cross-file claims unverified):\n" + summary
            + "\n\nSUSPECT FILES:\n\n" + files_block(picked))
    messages = [{"role": "system", "content": sp},
                {"role": "user", "content": user}]
    try:
        text, _usage, body2 = complete(api_key, api_url, model, messages,
                                        deep_spec, session=session)
    except (ChatError, NetworkError) as err:
        print(f"ambient: cross-file pass failed "
              f"({getattr(err, 'diagnosis', None) or err}) — pass-1 "
              "findings stand unchanged", file=sys.stderr)
        return final
    if not structured:
        return (final
                + "\n\n===== CROSS-FILE CONFIRMATION (one bounded second "
                  "pass over: " + ", ".join(p for p, _s in picked)
                + ") =====\n" + text)
    obj2 = extract_json(text)
    if obj2 is None or not isinstance(obj2.get("findings"), list):
        print("ambient: cross-file pass returned unparseable output — "
              "pass-1 findings stand unchanged", file=sys.stderr)
        return final
    obj1 = extract_json(final) or {}
    # _as_pos_int coerces UNTRUSTED counts (obj2 is raw model JSON; a
    # non-numeric _repaired_chunks would crash int()) to a safe non-negative int.
    # M38: the second pass itself can be truncated/JSON-repaired — fold its
    # state in, else the merged verdict over-states completeness (a truncated
    # confirmation pass would read as clean).
    incomplete = (obj2.get("_repaired") or obj2.get("_repaired_chunks")
                  or (isinstance(body2, dict)
                      and (body2.get("finish_reason") == "length"
                           or body2.get("salvaged_partial"))))
    return json.dumps(_audit_core.merge_cross_file_findings(
        obj1, obj2, incomplete, dedupe=dedupe_findings,
        verdict=_verdict_from, as_pos_int=_as_pos_int))


def git_diff_inputs(staged, ref, deps=None):
    """Build audit inputs from a git diff PLUS the full current content of each
    changed file — a diff-only audit misses bugs that depend on unchanged
    code. Returns (labeled, diff_text) or exits cleanly outside a repo."""
    ABS_MAX_CHARS = deps.ABS_MAX_CHARS
    EXIT_USAGE = deps.EXIT_USAGE
    _fail_exit = deps._fail_exit
    _intake_core = deps._intake_core
    _repository_core = deps._repository_core
    sanitize = deps.sanitize
    subprocess = deps.subprocess
    sys = deps.sys
    snapshot, failure = _repository_core.capture_git_diff(
        staged,
        ref,
        subprocess.Popen,
        subprocess.TimeoutExpired,
        ABS_MAX_CHARS,
    )
    if failure is not None:
        if failure.usage:
            _fail_exit(
                None, "audit", failure.category, failure.message,
                exit_code=EXIT_USAGE,
            )
        _fail_exit(None, "audit", failure.category, failure.message)
    for path in snapshot.omitted_paths:
        shown = path if all(char.isprintable() for char in path) else ascii(path)
        print(f"ambient: skipping {shown} (outside repo root)", file=sys.stderr)
    if len(snapshot.diff_text) > ABS_MAX_CHARS:
        _fail_exit(
            None, "audit", "input",
            f"git diff exceeds the {ABS_MAX_CHARS:,}-character input ceiling; "
            "narrow the revision range.",
        )
    remaining = max(1, ABS_MAX_CHARS - len(snapshot.diff_text))
    full_paths = tuple(full for _label, full in snapshot.changed_files)
    chunks, warnings, overflow_path = _intake_core.read_files(full_paths, remaining)
    for warning in warnings:
        print(sanitize(f"ambient: {warning}"), file=sys.stderr)
    if overflow_path is not None:
        shown_path = sanitize(overflow_path)
        _fail_exit(
            None, "audit", "input",
            f"git diff plus changed-file context exceeds the "
            f"{ABS_MAX_CHARS:,}-character input ceiling at {shown_path}; "
            "narrow the revision range or audit files separately.",
        )
    labels = {full: label for label, full in snapshot.changed_files}
    pairs = tuple((labels.get(path, path), text) for path, text in chunks)
    guttered = _repository_core.with_line_gutters(pairs)
    total = len(snapshot.diff_text) + sum(len(text) for _label, text in guttered)
    if total > ABS_MAX_CHARS:
        _fail_exit(
            None, "audit", "input",
            f"git diff plus line-numbered changed files exceeds the "
            f"{ABS_MAX_CHARS:,}-character input ceiling; narrow the revision "
            "range or audit files separately.",
        )
    return [("DIFF (git)", snapshot.diff_text), *guttered]
