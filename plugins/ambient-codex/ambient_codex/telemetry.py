"""Read-only observed chars-per-token telemetry derived from local usage."""

import json


def observed_cpt(model, enabled, usage_path, cache, minimum, maximum, alpha):
    """Return ``(observed_value, updated_cache)`` without mutating ``cache``.

    Estimated usage never contributes to sizing.  Corrupt, missing, or
    untrusted records are ignored so local telemetry can never block a run.
    """
    if not model or not enabled:
        return None, cache
    table = dict(cache) if cache is not None else {}
    if cache is None:
        try:
            with open(usage_path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict) or record.get("est"):
                        continue
                    model_id = record.get("model")
                    chars, input_tokens = record.get("chars"), record.get("in")
                    if (not isinstance(model_id, str)
                            or isinstance(chars, bool)
                            or isinstance(input_tokens, bool)
                            or not isinstance(chars, (int, float))
                            or not isinstance(input_tokens, (int, float))
                            or not 0 < chars < float("inf")
                            or not 0 < input_tokens < float("inf")):
                        continue
                    ratio = max(minimum, min(maximum, chars / input_tokens))
                    previous = table.get(model_id)
                    table[model_id] = ratio if previous is None else (
                        alpha * ratio + (1 - alpha) * previous)
        except OSError:
            pass
    value = table.get(model)
    if value is None:
        return None, table
    return max(minimum, min(maximum, value)), table


__all__ = ("observed_cpt",)
