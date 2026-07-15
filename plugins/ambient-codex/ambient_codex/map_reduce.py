"""Pure planning helpers for the map-reduce orchestration facade."""

import re


def files_block(chunks):
    """Render labeled input files with explicit, model-visible boundaries."""
    return "\n\n".join(f"===== FILE: {path} =====\n{text}"
                       for path, text in chunks)


def chunk_ranges(chunk_text):
    """Return packed-block coverage labels from a chunk body."""
    return [match.group(1).strip()
            for match in re.finditer(r"===== (.+?) =====", chunk_text or "")]


def code_map_budget(single_shot_chars, default_budget, maximum_budget):
    """Scale a repository-map budget to the model's single-shot capacity."""
    if not single_shot_chars or single_shot_chars <= 0:
        return default_budget
    return max(512, min(single_shot_chars // 10, maximum_budget))


def map_note(map_system, code_map, chunk_count, index_marker):
    """Build the shared per-chunk system prompt before index substitution."""
    return (
        map_system
        + (f"\n\n{code_map}" if code_map else "")
        + f"\n\n(You are seeing chunk {index_marker} of {chunk_count} of a "
        "larger input. File boundaries may be split across chunks — flag "
        "suspected split artifacts as such instead of guessing.)"
    )


def group_for_budget(texts, budget):
    """Keep input order while forming maximal groups within a char budget.

    A single oversized item remains its own group: the caller must preserve it
    rather than drop paid-for model output just because no further merge is
    possible.
    """
    groups, current, current_length = [], [], 0
    for text in texts:
        if current and current_length + len(text) > budget:
            groups.append(current)
            current, current_length = [], 0
        current.append(text)
        current_length += len(text)
    if current:
        groups.append(current)
    return groups


def resolve_parallel(values, default, minimum=1, maximum=16):
    """Select the first valid bounded fan-out width from ordered inputs."""
    for raw in values:
        if raw is None or raw == "":
            continue
        try:
            return max(minimum, min(maximum, int(raw)))
        except (TypeError, ValueError):
            continue
    return default


def reduce_response_format(response_format, profile, *, response_format_for):
    """Re-gate a structured response request to the reduce-model capability."""
    if not response_format:
        return response_format
    if response_format.get("type") == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema") or {}
        return response_format_for(profile, schema)
    if response_format.get("type") == "json_object":
        features = profile.features or []
        return response_format if {"json_mode", "structured_outputs"} & set(features) else None
    return response_format


def coverage_gap(errors, truncated):
    """Describe incomplete map coverage for the synthesis model."""
    gap = ""
    if errors:
        gap += (
            f"\n\nCOVERAGE GAP: {len(errors)} chunk(s) FAILED (missing entirely): "
            f"{'; '.join(errors)}. State this gap at the TOP and do NOT issue a "
            "clean/SHIP verdict."
        )
    if truncated:
        gap += (
            f"\n\nCOVERAGE GAP: chunk(s) {truncated} were TRUNCATED (partial). "
            "Treat their coverage as incomplete and do NOT issue a clean verdict."
        )
    return gap


def hierarchical_reduce(texts, *, effective_budget, merge):
    """Merge ordered partials within budget, retaining incomplete merges."""
    current = list(texts)
    synth_failed = False
    while len(current) > 1 and sum(len(text) for text in current) > effective_budget:
        groups = group_for_budget(current, effective_budget)
        if all(len(group) == 1 for group in groups):
            break
        next_texts = []
        for group in groups:
            if len(group) == 1:
                next_texts.append(group[0])
            else:
                merged, complete = merge(group)
                synth_failed = synth_failed or not complete
                next_texts.append(merged)
        current = next_texts
    if len(current) > 1:
        final, complete = merge(current)
        return final, synth_failed or not complete
    return current[0], synth_failed


def partial_reason(*, errors, truncated, synth_failed, missed_ranges, chunk_count):
    """Return the partial flag and concise user-facing incomplete-work reason."""
    partial = bool(errors or truncated or synth_failed)
    reasons = []
    if errors:
        detail = "; ".join(error[:500] for error in errors[:3])
        if len(errors) > 3:
            detail += f"; +{len(errors) - 3} more"
        reasons.append(f"{len(errors)} of {chunk_count} chunks failed ({detail})")
    if truncated:
        reasons.append(f"{len(truncated)} chunk(s) truncated")
    if synth_failed:
        reasons.append("synthesis was incomplete (truncated or fell back to raw concatenation)")
    if missed_ranges:
        reasons.append("UNREVIEWED: " + "; ".join(dict.fromkeys(missed_ranges)))
    return partial, "; ".join(reasons)


def _cancel_executor(pool):
    """Stop queued work while allowing in-flight workers to unwind."""
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:  # Python 3.8
        pool.shutdown(wait=False)


def collect_fanout(chunks, *, work, width, cancel_event, chunk_ranges,
                   executor, as_completed):
    """Collect ordered map results while recording ordinary worker failures."""
    results, errors, missed_ranges = [None] * len(chunks), [], []
    pool = executor(max_workers=width)
    aborted = False
    try:
        futures = {pool.submit(work, index): index for index in range(len(chunks))}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as error:  # one failed chunk preserves sibling work
                coverage = chunk_ranges(chunks[index])
                missed_ranges.extend(coverage)
                where = f" [{'; '.join(coverage)}]" if coverage else ""
                errors.append(f"chunk {index + 1}{where}: {type(error).__name__}: {error}")
            except BaseException:
                raise
    except BaseException:
        aborted = True
        cancel_event.set()
        _cancel_executor(pool)
        raise
    finally:
        if not aborted:
            pool.shutdown(wait=True)
    return results, errors, missed_ranges


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def run_chunk(index, *, chunks, map_note, index_marker, model, spec, session,
              cancel_event, gate, cache_key, cache_get, cache_put, cache_ttl,
              complete, chat_error, retry_delay, sleep, use_cache):
    """Run one cached map chunk with cooperative cancellation and rate retry."""
    if cancel_event.is_set():
        raise chat_error("cancelled", "fan-out cancelled before this chunk started")
    system = map_note.replace(index_marker, str(index + 1))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": chunks[index]}]
    key = cache_key(model, system, chunks[index], spec.max_tokens, spec.temperature,
                    spec.response_format, salt=spec._cache_salt)
    cached = cache_get(key, cache_ttl) if use_cache else None
    if cached is not None:
        return cached, False, True
    hold = gate if gate is not None else _NoopContext()
    for attempt in range(3):
        try:
            with hold:
                if cancel_event.is_set():
                    raise chat_error("cancelled", "fan-out cancelled while waiting for a slot")
                text, _usage, body = complete(model, messages, spec, session=session)
            partial = bool(body.get("salvaged_partial")) or body.get("finish_reason") == "length"
            if use_cache and not partial and body.get("_served_model", model) == model:
                cache_put(key, text)
            return text, partial, False
        except chat_error as error:
            if error.category == "rate" and attempt < 2 and not cancel_event.is_set():
                retry_after = getattr(error, "retry_after", None)
                sleep(retry_delay(3 * (attempt + 1),
                                  {"Retry-After": retry_after} if retry_after else None))
                continue
            raise
    raise AssertionError("chunk retry loop exhausted unexpectedly")


def synthesize_parts(texts, *, system, gap, model, spec, session, complete,
                     recoverable_errors):
    """Synthesize ordered chunk reports, retaining raw paid-for work on failure."""
    body = "\n\n".join(
        f"----- PART {index + 1} -----\n{text}" for index, text in enumerate(texts))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": body + gap}]
    try:
        text, _usage, response = complete(model, messages, spec, session=session)
        complete_response = not (response.get("finish_reason") == "length"
                                 or bool(response.get("salvaged_partial")))
        return text, complete_response
    except recoverable_errors:
        return body, False


__all__ = ("files_block", "chunk_ranges", "code_map_budget", "map_note",
           "group_for_budget", "resolve_parallel", "reduce_response_format",
           "coverage_gap", "hierarchical_reduce", "partial_reason", "collect_fanout",
           "run_chunk", "synthesize_parts")
