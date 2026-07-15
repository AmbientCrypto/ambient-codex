"""Catalog transport, model coercion, capability formats, and output estimates."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .constants import DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class ModelRuntimeDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def api_request(api_url, api_key, path, payload=None, timeout=DEFAULT_TIMEOUT_S, deps=None):
    """Compatibility wrapper for the extracted HTTP transport operation."""
    _retry_delay = deps._retry_delay
    _transport = deps._transport
    sys = deps.sys
    time = deps.time
    return _transport.api_request(
        api_url, api_key, path, payload, timeout,
        retry_delay=_retry_delay, sleep=time.sleep, stderr=sys.stderr,
    )


def _catalog_data(body, deps=None):
    """Compatibility wrapper for extracted catalog normalization."""
    _transport = deps._transport
    return _transport.catalog_data(body)


def fetch_models(api_url, api_key, deps=None):
    _catalog_data = deps._catalog_data
    api_request = deps.api_request
    error_message = deps.error_message
    redact = deps.redact
    set_pricing_catalog = deps.set_pricing_catalog
    sys = deps.sys
    status, body = api_request(api_url, api_key, "/v1/models", timeout=30)
    if status != 200:
        sys.exit(
            "ambient: /v1/models failed "
            f"(HTTP {status}): {redact(error_message(body), api_key)}"
        )
    # Normalize ONCE at the choke point: every catalog consumer downstream may
    # assume dict entries with a string id (a bare-string or
    # id-less entry crashed _dedupe_catalog/auth_probe). Field VALUES are still
    # coerced at use (_as_pos_int etc.).
    models = _catalog_data(body)
    if models:
        # Memoize for post-run pricing (savings receipt / log_usage) — never
        # overwrite a good memo with a degraded empty catalog.
        set_pricing_catalog(models)
    return models


def _as_pos_int(v, default, deps=None):
    """Coerce an untrusted catalog field to a positive int, else `default`.
    The catalog is network data from a decentralized API — one drifted field
    type (string context_length, bool, dict) must never crash every command
."""
    _model_config = deps._model_config
    return _model_config.as_pos_int(v, default)


def _as_bool(v, deps=None):
    """Coerce an untrusted catalog READINESS flag. A raw truthiness test let a
    drifted string `"false"`/`"0"` read as READY (any non-empty string is
    truthy). bool → itself; str → false for the usual falsey words; else bool()."""
    _model_config = deps._model_config
    return _model_config.as_bool(v)


def ready_model_ids(models, deps=None):
    _model_config = deps._model_config
    return _model_config.ready_model_ids(models)


def safe_catalog(api_url, api_key, deps=None):
    """Fetch the model catalog, degrading to [] instead of exiting on failure."""
    NetworkError = deps.NetworkError
    fetch_models = deps.fetch_models
    try:
        return fetch_models(api_url, api_key)
    except (NetworkError, SystemExit):
        return []


def model_pricing(catalog, model, deps=None):
    """Facade wrapper: pure pricing lookup lives in
    ``ambient_codex.usage_pricing``."""
    _usage_pricing = deps._usage_pricing
    return _usage_pricing.model_pricing(catalog, model)


def adaptive_response_format(model, profile, schema, deps=None):
    """response_format for a structured call, adjusted by LEARNED behavior.
    If `model` has proven UNRELIABLE at honoring structured output, skip the
    strict json_schema it ignores and return None so the caller takes the
    prose+parser path directly (no wasted first call). Otherwise identical to
    response_format_for (optimistic: try strict first, learn from the result)."""
    cap_state = deps.cap_state
    response_format_for = deps.response_format_for
    if cap_state(model, "structured_json") == "unreliable":
        return None
    return response_format_for(profile, schema)


def downgrade_response_format(rf, profile, deps=None):
    """The next-looser structured-output demand after `rf` failed: strict
    json_schema -> json_object (when the model supports it) -> None
    (prompt-only). A model that ignores a strict schema often complies with a
    looser ask, so a build/plan retry steps DOWN the ladder instead of
    re-sending the same doomed request."""
    if (rf and rf.get("type") == "json_schema"
            and "json_mode" in (getattr(profile, "features", None) or [])):
        return {"type": "json_object"}
    return None


def _served_model_of(meta, default, deps=None):
    """The model that actually served a completion, from its metadata — or
    `default`. Guards a malformed/non-dict `meta` and a non-string served id
    (Codex round 2: a bogus _b could crash or record under a junk key)."""
    if isinstance(meta, dict):
        served = meta.get("_served_model")
        if isinstance(served, str) and served:
            return served
    return default


def build_plan_rf_ladder(model, profile, deps=None):
    """Ordered, de-duplicated response_format rungs to try for a build plan:
    the adaptive first choice, then each strictly-looser fallback, ending in
    prompt-only (None). A capable strict+json model gets json_schema ->
    json_object -> None (Codex: the old 2-entry ladder skipped prompt-only); a
    learned-unreliable model goes straight to prompt-only. Always >= 2 entries
    so the reminder-retry still fires."""
    BUILD_PLAN_SCHEMA = deps.BUILD_PLAN_SCHEMA
    adaptive_response_format = deps.adaptive_response_format
    downgrade_response_format = deps.downgrade_response_format
    def _t(rf):
        return None if rf is None else rf.get("type")
    rf = adaptive_response_format(model, profile, BUILD_PLAN_SCHEMA)
    ladder, guard = [], 0
    while guard < 4:
        guard += 1
        if not ladder or _t(ladder[-1]) != _t(rf):
            ladder.append(rf)
        if rf is None:
            break
        rf = downgrade_response_format(rf, profile)
    if len(ladder) < 2:
        ladder.append(ladder[-1])  # a second (reminder-)retry on the same rung
    return ladder


def _expected_output_tokens(catalog, model, max_tokens, deps=None):
    """Conservative expected output for spend gating.

    Ambient-compatible reasoning models account for internal reasoning inside
    the completion budget and routinely consume far more than the final answer
    text. Reserve their full requested budget. A missing catalog entry gets the
    same conservative treatment; only a known non-reasoning model uses the
    normal answer reserve.
    """
    ANSWER_TOKENS_RESERVE = deps.ANSWER_TOKENS_RESERVE
    meta = next(
        (item for item in (catalog or [])
         if isinstance(item, dict) and item.get("id") == model),
        None,
    )
    if meta is None:
        return max_tokens
    raw_features = meta.get("supported_features")
    features = raw_features if isinstance(raw_features, list) else []
    if "reasoning" in features:
        return max_tokens
    return min(max_tokens, ANSWER_TOKENS_RESERVE)


def estimate_cost(catalog, model, input_chars, n_calls, max_tokens, deps=None):
    """(expected, bound, assumed) dollar estimates for a run. `bound` assumes
    every call emits its FULL max_tokens budget (the old, 10-30x-pessimistic
    figure that over-refused big legitimate jobs); `expected` uses the full
    output budget for reasoning or unknown models and a normal answer reserve
    for known direct-answer models. Unpriced model / missing catalog →
    worst-case ASSUMED prices with assumed=True, so the ceiling still applies.
    Input tokens use the CONSERVATIVE cost cpt (_cost_cpt = min(observed,
    static) — telemetry may only tighten the gate, never under-price)."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _cost_cpt = deps._cost_cpt
    _expected_output_tokens = deps._expected_output_tokens
    model_pricing = deps.model_pricing
    price = model_pricing(catalog, model)
    assumed = price is None
    if assumed:
        price = (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE)
    in_tok = input_chars / _cost_cpt(model)
    # Input is billed roughly ONCE total (each chunk sends only its own slice),
    # plus ~30% for the synthesis pass re-sending the partial results.
    input_cost = in_tok * 1.3 * price[0]
    bound = (input_cost + n_calls * max_tokens * price[1]) / 1e6
    expected_out = _expected_output_tokens(catalog, model, max_tokens)
    expected = (input_cost + n_calls * expected_out * price[1]) / 1e6
    return expected, bound, assumed
