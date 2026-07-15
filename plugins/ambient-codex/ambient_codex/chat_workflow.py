"""Pure state policies used by the interactive chat workflow."""

import json


def trim_history(history, budget_chars):
    """Return a new recent-first-fitting history without mutating the caller."""
    kept = list(history)

    def size(messages):
        return sum(len(message.get("content") or "") for message in messages)

    while len(kept) > 2 and size(kept) > budget_chars:
        kept = kept[2:]
    while len(kept) > 1 and size(kept) > budget_chars:
        kept = kept[1:]
    return kept


def _stream_delta_handler(stdout, redactor, streamed_parts):
    """Create a streaming callback that redacts terminal writes."""
    def on_delta(piece):
        out = redactor.feed(piece)
        if out:
            stdout.write(out)
            stdout.flush()
        streamed_parts.append(piece)

    return on_delta


def single_shot_response(api_key, api_url, model, messages, args, *, kind,
                         session, session_or, request_spec, stdout,
                         stream_redactor, complete, failure, completion_error,
                         emit_json, redact, render):
    """Run and render one completion through injected facade collaborators."""
    session = session_or(session, api_key, api_url)
    api_key, api_url = session.api_key, session.api_url
    spec = request_spec(args)
    want_json = spec.json
    live_stream = stdout.isatty() and not spec.raw and not want_json
    streamed_parts = []
    redactor = stream_redactor(api_key)
    on_delta = _stream_delta_handler(stdout, redactor, streamed_parts)

    try:
        content, usage, body = complete(
            api_key, api_url, model, messages, spec,
            on_delta=on_delta if live_stream else None, session=session)
    except completion_error as err:
        if streamed_parts:
            stdout.write(redactor.flush())
            stdout.write("\n")
            stdout.flush()
        failure(spec, kind, err, api_key)
        return
    if streamed_parts:
        stdout.write(redactor.flush())
        stdout.flush()
    if want_json:
        served = body.get("_served_model", model)
        emit_json(
            kind, model=served, api_key=api_key, content=content,
            partial=bool(body.get("salvaged_partial")),
            reason="output salvaged" if body.get("salvaged_partial") else None,
            usage=body.get("usage"), finish_reason=body.get("finish_reason"),
            extra={"requested_model": model} if served != model else None,
            allow_partial=spec.allow_partial)
        return
    if spec.raw:
        print(redact(json.dumps(body, indent=2), api_key))
        return
    clean_stream = bool(streamed_parts) and "".join(streamed_parts).strip() == content
    if not clean_stream and streamed_parts:
        stdout.write("\n[ambient: stream restarted — full result below]\n")
        stdout.flush()
    render(
        content,
        bool(body.get("salvaged_partial")) or body.get("finish_reason") == "length",
        "output was salvaged/truncated", spec, api_key, usage,
        body.get("_served_model", model), already_streamed=clean_stream)


__all__ = ("trim_history", "single_shot_response")
