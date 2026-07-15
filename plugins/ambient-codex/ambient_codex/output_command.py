"""Terminal and JSON result rendering composition."""

import json
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class OutputDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def reasoning_hint(content, completion_tokens, deps):
    """A short receipt tag when billed output tokens materially exceed the
    visible answer: a reasoning model bills its hidden thinking as output, so a
    one-word reply can read as out=150 and look like a metering bug. Keeps the
    receipt honest without pretending to know the exact reasoning split."""
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    try:
        visible = len(content or "") / CHARS_PER_TOKEN
        if completion_tokens and completion_tokens > max(20, visible * 2):
            return " incl. reasoning"
    except (TypeError, ValueError):
        pass
    return ""


def render_result(text, partial, reason, args, api_key, usage=None, model=None,
                  already_streamed=False, usage_by_model=None, deps=None):
    """Single place that prints a result and sets the exit code. Partial/
    incomplete results get a loud header and a non-zero exit unless the caller
    passed --allow-partial, so a truncated audit can never masquerade as clean.
    `already_streamed` = the body was live-streamed to stdout, so don't
    reprint it — just close the line, warn, and set the exit code.
    `usage_by_model`: per-SERVED-model token split for runs whose
    samples were served by different models — the receipt then prices each
    model's own tokens instead of the aggregate at `model`'s price."""
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    _output_schema = deps._output_schema
    _reasoning_hint = deps._reasoning_hint
    paint = deps.paint
    redact = deps.redact
    savings_note = deps.savings_note
    savings_note_by_served = deps.savings_note_by_served
    return _output_schema.render_terminal_result(
        text, partial, reason, args, api_key, usage=usage, model=model,
        already_streamed=already_streamed, usage_by_model=usage_by_model,
        paint=paint, redact=redact, stdout=sys.stdout, emit_stdout=print,
        emit_stderr=lambda value: print(value, file=sys.stderr),
        exit_process=sys.exit, partial_exit_code=EXIT_PARTIAL,
        savings_note=savings_note, savings_note_by_served=savings_note_by_served,
        reasoning_hint=_reasoning_hint,
    )


def public_usage(usage, deps):
    """Only token counts and the estimation marker are safe to emit. A provider
    response may attach cost/price/saved_pct metadata to `usage`; those never
    leave the tool (founder hard rule: never surface money, and the savings
    comparison is opt-in)."""
    _output_schema = deps._output_schema
    return _output_schema.public_usage(usage)


def emit_json(kind, *, model, api_key="", content=None, findings=None,
              verdict=None, partial=False, reason=None, usage=None,
              finish_reason=None, extra=None, allow_partial=False,
              exit_now=True, deps=None):
    """THE machine-readable sink: every --json surface — ask,
    code, audit, consensus, build, single-shot or map-reduce — emits this one
    shape, so an orchestrator never special-cases how a result was computed
. schema_version bumps on any
    breaking change. Returns the exit code; exits itself unless exit_now=False."""
    EXIT_PARTIAL = deps.EXIT_PARTIAL
    _output_schema = deps._output_schema
    redact = deps.redact
    savings_note = deps.savings_note
    env, code = _output_schema.build_envelope(
        kind, model=model, usage=usage, content=content, findings=findings,
        verdict=verdict, partial=partial, reason=reason,
        finish_reason=finish_reason, extra=extra,
        allow_partial=allow_partial, partial_exit_code=EXIT_PARTIAL,
    )
    print(redact(json.dumps(env, indent=2), api_key))
    if usage and model:
        # The savings receipt goes to STDERR in --json mode too (F05d): stdout
        # stays clean parseable JSON, and the value proposition is no longer
        # invisible to anyone who runs with --json. savings_note owns the
        # honesty rules (assumed pricing → no claim; est. label).
        note = savings_note(model, usage)
        print(redact(f"[ambient {model} | in={usage.get('prompt_tokens')} "
                     f"out={usage.get('completion_tokens')} tokens{note}]",
                     api_key), file=sys.stderr)
    if code and exit_now:
        sys.exit(code)
    return code


def json_mode(args):
    """True when the caller asked for the machine envelope: --json on
    ask/code/build, or audit's --format json (its --json is a const alias)."""
    return bool(getattr(args, "json", False)) \
        or getattr(args, "format", None) == "json"


def json_in_argv():
    """Best-effort json-mode detection from sys.argv, for usage_exit (which runs
    before/without a parsed args object)."""
    argv = sys.argv[1:]
    if "--json" in argv or "--format=json" in argv:
        return True
    return any(a == "--format" and argv[i + 1] == "json"
               for i, a in enumerate(argv[:-1]))


def emit_json_error(kind, category, diagnosis, api_key="", exit_code=1, deps=None):
    """Error twin of emit_json: an orchestrator that asked for --json must get
    a PARSEABLE failure on stdout ({"status": "error", …}), never a bare
    stderr line it has to scrape. Same schema_version; exits with exit_code
    (1 for a runtime failure, 64 for a usage error)."""
    _output_schema = deps._output_schema
    redact = deps.redact
    env = _output_schema.build_error_envelope(
        kind, category, redact(str(diagnosis), api_key), exit_code)
    # `ambient map --json` is a JSONL stream (one object per line) — its
    # terminal error envelope must stay ONE line like every item envelope,
    # or a line-by-line consumer breaks on the very failure it must parse.
    indent = None if kind == "map" else 2
    print(redact(json.dumps(env, indent=indent), api_key))
    sys.exit(exit_code)


def fail(args, kind, err, api_key="", deps=None):
    """The one exit for a ChatError/NetworkError CAUGHT inside a task handler
    (chat/ask/audit/build): under --json the orchestrator gets the machine
    envelope — parseable, redacted, exit 1 — instead of an unredacted prose
    line it has to scrape; the prose path stays byte-identical for humans.
    Errors that ESCAPE to main() already get this split there."""
    _json_mode = deps._json_mode
    emit_json_error = deps.emit_json_error
    category = getattr(err, "category", "network")
    diagnosis = getattr(err, "diagnosis", None) or str(err)
    if _json_mode(args):
        emit_json_error(kind, category, diagnosis, api_key)  # exits 1
    sys.exit(f"ambient [{category}]: {diagnosis}")


def argv_command():
    """The task command named on the command line, for envelope `kind` at exit
    sites with no parsed args object (shared helpers, argparse itself).
    Restricted to the --json-capable commands so `kind` stays a closed
    vocabulary; anything else reports as 'usage'."""
    for tok in sys.argv[1:]:
        if not tok.startswith("-"):
            return tok if tok in ("ask", "code", "audit", "build", "map") \
                else "usage"
    return "usage"


def fail_exit(args, kind, category, msg, exit_code=1, api_key="", prose=None, deps=None):
    """The one exit for every OTHER failure reachable under a --json-capable
    command (input refusals, git preconditions, spend gates, distillation
    gaps) — the failure contract is TOTAL: a --json caller always gets the
    machine envelope, never a bare stderr line to scrape. `args=None` sniffs
    sys.argv for helpers that run before/without a parsed args object. The
    prose path prints exactly what every prior release printed (default
    'ambient: {msg}'; pass `prose` where the historical line differs).
    exit_code is honored on BOTH paths: runtime failures keep the historical
    sys.exit(<string>) convention (stderr + exit 1); anything else (e.g.
    EX_USAGE=64) prints the same prose to stderr, then exits with that code —
    a usage error must not report as a runtime failure just because the
    caller didn't ask for --json."""
    _json_in_argv = deps._json_in_argv
    _json_mode = deps._json_mode
    emit_json_error = deps.emit_json_error
    wants_json = _json_mode(args) if args is not None else _json_in_argv()
    if wants_json:
        emit_json_error(kind, category, msg, api_key, exit_code=exit_code)
    prose_line = prose if prose is not None else f"ambient: {msg}"
    if exit_code == 1:
        sys.exit(prose_line)
    print(prose_line, file=sys.stderr)
    sys.exit(exit_code)
