"""Public JSON projection rules for Ambient CLI output."""


_PUBLIC_USAGE_KEYS = frozenset((
    "prompt_tokens", "completion_tokens", "total_tokens",
    "reasoning_tokens", "_estimated",
))


def public_usage(usage):
    """Return a fresh token-only usage mapping safe for public JSON output."""
    if not isinstance(usage, dict):
        return usage
    return {key: usage[key] for key in usage if key in _PUBLIC_USAGE_KEYS}


def build_envelope(kind, *, model, usage=None, content=None, findings=None,
                   verdict=None, partial=False, reason=None,
                   finish_reason=None, extra=None, allow_partial=False,
                   partial_exit_code=2):
    """Build one public result envelope and its process exit code."""
    truncated = finish_reason == "length"
    partial = bool(partial or truncated)
    if truncated:
        reason = (reason + "; " if reason else "") + "output hit the token cap"
    exit_code = partial_exit_code if (partial and not allow_partial) else 0
    envelope = {
        "schema_version": 1,
        "kind": kind,
        "status": "partial" if partial else "ok",
        "model": model,
        "partial": partial,
        "coverage_gap": reason or None,
        "finish_reason": finish_reason,
        "usage": public_usage(usage),
        "exit_code": exit_code,
    }
    if content is not None:
        envelope["content"] = content
    if findings is not None:
        envelope["findings"] = findings
        envelope["verdict"] = verdict
    if extra:
        envelope.update(extra)
    return envelope, exit_code


def build_error_envelope(kind, category, diagnosis, exit_code):
    """Build a public error envelope after the facade has redacted text."""
    return {
        "schema_version": 1,
        "kind": kind,
        "status": "error",
        "category": category,
        "diagnosis": diagnosis,
        "exit_code": exit_code,
    }


def render_terminal_result(text, partial, reason, request, api_key, *, usage,
                           model, already_streamed, usage_by_model, paint,
                           redact, stdout, emit_stdout, emit_stderr,
                           exit_process, partial_exit_code, savings_note,
                           savings_note_by_served, reasoning_hint):
    """Render terminal output while keeping financial values out of the schema."""
    if partial:
        header = (paint("⚠ PARTIAL / INCOMPLETE RESULT", "1;33")
                  + f" — {reason}. This is NOT a clean pass; "
                  "re-run (optionally with a larger --max-tokens/--timeout, or a bigger-"
                  "context model) or pass --allow-partial to accept.\n\n")
        if already_streamed:
            stdout.write("\n")
            stdout.flush()
            emit_stderr(redact(header.strip(), api_key))
        else:
            emit_stdout(redact(header + text, api_key))
        if not getattr(request, "allow_partial", False):
            exit_process(partial_exit_code)
        return
    if already_streamed:
        if not text.endswith("\n"):
            stdout.write("\n")
            stdout.flush()
    else:
        emit_stdout(redact(text, api_key))
    if usage and model:
        note = (savings_note_by_served(usage_by_model) if usage_by_model
                else savings_note(model, usage))
        hint = reasoning_hint(text, usage.get("completion_tokens"))
        emit_stderr(redact(
            f"\n[ambient {model} | in={usage.get('prompt_tokens')} "
            f"out={usage.get('completion_tokens')} tokens{hint}{note}]", api_key))


__all__ = ("public_usage", "build_envelope", "build_error_envelope",
           "render_terminal_result")
