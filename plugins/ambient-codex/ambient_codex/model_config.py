"""Model preference resolution and defensive catalog field coercion.

These helpers are pure once the caller supplies its environment and default
model identifiers.  They intentionally do not fetch a catalog or read config
files, so selection policy stays testable and free of import-time effects.
"""


def model_map(conf, environ):
    """Parse the user's comma-separated per-phase model routing map."""
    raw = environ.get("AMBIENT_MODEL_MAP") or conf.get("AMBIENT_MODEL_MAP") or ""
    result = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key and value:
            result[key] = value
    return result


def resolve_model(args, conf, kind, phase, environ, default_model,
                  default_code_model):
    """Resolve an explicit model, phase routing, saved default, or fallback."""
    env_key = "AMBIENT_CODE_MODEL" if kind == "code" else "AMBIENT_MODEL"
    fallback = default_code_model if kind == "code" else default_model
    return (
        getattr(args, "model", None)
        or model_map(conf, environ).get(phase or kind)
        or environ.get(env_key)
        or conf.get(env_key)
        or fallback
    )


def as_pos_int(value, default):
    """Coerce one untrusted positive integer field or return ``default``."""
    try:
        if isinstance(value, bool):
            return default
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def as_bool(value):
    """Coerce an untrusted readiness flag without treating ``'false'`` as true."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


def ready_model_ids(models):
    """Return only nonempty identifiers marked ready in an untrusted catalog."""
    return [model.get("id") for model in models
            if isinstance(model, dict)
            and as_bool(model.get("is_ready"))
            and model.get("id")]


__all__ = ("model_map", "resolve_model", "as_pos_int", "as_bool",
           "ready_model_ids")
