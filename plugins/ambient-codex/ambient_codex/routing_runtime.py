"""Automatic routing, reduce selection, profiles, and output-budget composition."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class RoutingDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def is_auto_model(model, deps=None):
    """True for the explicit `auto` pseudo-model specs (case-insensitive)."""
    _routing_core = deps._routing_core
    return _routing_core.is_auto_model(model)


def _catalog_out_price(catalog, model_id, deps=None):
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    model_pricing = deps.model_pricing
    p = model_pricing(catalog, model_id)
    return p[1] if p else ASSUMED_MAX_OUTPUT_PRICE


def resolve_auto_model(spec, catalog, conf, input_chars=0, args=None,
                       label="-m", deps=None):
    """Resolve the EXPLICIT `-m auto[:cheapest|:largest]` pseudo-model to a
    REAL model id at call time: READY + curation-visible + text-output, and
    (for cheapest/bare auto) able to hold the input in ONE pass. The pick is
    PRINTED to stderr — the user delegated the choice but always sees exactly
    which model runs. SACRED-safe: this only ever executes when the user
    literally asked for auto. Fails with the clean [model] diagnosis (naming
    what IS ready) when nothing qualifies — never a silent substitute.
    `label` names the surface the spec came from in every message (-m,
    --reduce-model, AMBIENT_MODEL_MAP reduce=)."""
    _argv_command = deps._argv_command
    _as_bool = deps._as_bool
    _as_pos_int = deps._as_pos_int
    _catalog_out_price = deps._catalog_out_price
    _fail_exit = deps._fail_exit
    _humanize_ctx = deps._humanize_ctx
    _routing_core = deps._routing_core
    curation = deps.curation
    is_hidden = deps.is_hidden
    model_profile = deps.model_profile
    ready_model_ids = deps.ready_model_ids
    sys = deps.sys
    spec_l = spec.strip().lower()
    variant = spec_l.split(":", 1)[1] if ":" in spec_l else ""
    allow, hide, show, _notes = curation(conf)
    candidates = []
    for m in catalog or []:
        if not (isinstance(m, dict) and _as_bool(m.get("is_ready")) and m.get("id")):
            continue
        out_mod = m.get("output_modalities") or ["text"]
        if not isinstance(out_mod, list) or "text" not in out_mod:
            continue
        if is_hidden(m["id"], allow, hide, show):
            continue
        candidates.append(m)

    def _bail(msg):
        _fail_exit(args, _argv_command(), "model", msg,
                   prose=f"ambient [model]: {msg}")

    if not candidates:
        ready = ready_model_ids(catalog)
        if ready:  # READY models exist but curation hides every one of them
            _bail(f"{label} {spec}: every READY model is hidden by your curation "
                  f"(READY: {', '.join(ready)}) — surface one with "
                  "`ambient-codex curate show <id>` or pick it explicitly with -m.")
        _bail(f"{label} {spec}: no model is serving at this moment — the "
              "network scales up with demand; try again shortly, or check: "
              "ambient-codex models")
    selected = _routing_core.select_auto_model(
        spec, candidates,
        is_ready=lambda _model: True,
        is_hidden=lambda _model: False,
        context_length=lambda candidate: _as_pos_int(
            candidate.get("context_length"), 0),
        output_price=lambda candidate: _catalog_out_price(catalog, candidate["id"]),
        fits=lambda candidate: input_chars <= model_profile(
            catalog, candidate["id"]).single_shot_chars,
    )
    if selected is None:
        if variant != "largest":
            names = ", ".join(m["id"] for m in candidates)
            _bail(f"{label} {spec}: no READY model fits this input "
                  f"(~{input_chars:,} chars) in one pass — READY: {names}; "
                  "pick one explicitly with -m (oversize inputs are "
                  "map-reduced), or use -m auto:largest.")
        _bail(f"{label} {spec}: no model is serving at this moment — the "
              "network scales up with demand; try again shortly, or check: "
              "ambient-codex models")
    pick, reason = selected
    if variant == "largest":
        chosen = next(candidate for candidate in candidates if candidate["id"] == pick)
        why = (f"largest READY context, "
               f"{_humanize_ctx(_as_pos_int(chosen.get('context_length'), 0))}")
    else:
        why = reason
    print(f"ambient: {label} {spec} -> {pick} ({why})", file=sys.stderr)
    return pick


def preflight_hint(model, catalog, conf, input_chars=0, deps=None):
    """ADVISORY ONLY. One stderr line when the user's resolved
    CONCRETE model has no live workers, or the whole input cannot fit its
    context window — naming READY alternatives. It never
    changes the model, never blocks, and says so: model choice is SACRED."""
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    _as_bool = deps._as_bool
    _as_pos_int = deps._as_pos_int
    _catalog_out_price = deps._catalog_out_price
    curation = deps.curation
    is_hidden = deps.is_hidden
    sys = deps.sys
    if not catalog:
        return None  # degraded/offline catalog — nothing trustworthy to say
    meta = next((m for m in catalog
                 if isinstance(m, dict) and m.get("id") == model), None)
    if meta is None:
        return None  # unknown id: complete() gives the real [model] diagnosis
    problems = []
    if not _as_bool(meta.get("is_ready")):
        problems.append(f"'{model}' isn't serving right now "
                        "(Ambient scales models with demand)")
    if input_chars:
        ctx_chars = _as_pos_int(meta.get("context_length"), 0) * CHARS_PER_TOKEN
        if ctx_chars and input_chars > ctx_chars:
            problems.append(
                f"the input (~{input_chars:,} chars) exceeds '{model}'s whole "
                "context window (it will be split and map-reduced)")
    if not problems:
        return None
    allow, hide, show, _notes = curation(conf)
    alts = []
    for m in catalog:
        if not (isinstance(m, dict) and _as_bool(m.get("is_ready")) and m.get("id")) \
                or m["id"] == model:
            continue
        out_mod = m.get("output_modalities") or ["text"]
        if not isinstance(out_mod, list) or "text" not in out_mod:
            continue
        if is_hidden(m["id"], allow, hide, show):
            continue
        alts.append((_catalog_out_price(catalog, m["id"]), m["id"]))
    alts.sort()
    line = "ambient: note — " + "; ".join(problems)
    if alts:
        shown = ", ".join(mid for _pr, mid in alts[:4])
        line += f" — READY: {shown}; pick with -m or -m auto"
    line += ". (advisory only — your model choice is unchanged)"
    print(line, file=sys.stderr)
    return None


def route_model(args, conf, kind, catalog, input_chars=0, phase=None, deps=None):
    """The shared command preamble: resolve the model, expand an
    explicit `auto` spec to a concrete READY pick (printed), or print the
    pre-flight advisory for a concrete model. Returns the model to run.
    NEVER changes a concrete model — the hint is information only."""
    is_auto_model = deps.is_auto_model
    preflight_hint = deps.preflight_hint
    resolve_auto_model = deps.resolve_auto_model
    resolve_model = deps.resolve_model
    model = resolve_model(args, conf, kind, phase=phase)
    if is_auto_model(model):
        return resolve_auto_model(model, catalog, conf, input_chars, args)
    preflight_hint(model, catalog, conf, input_chars)
    return model


def _validate_reduce_id(chosen, catalog, args, source, deps=None):
    """H1: an unknown reduce id must be diagnosed BEFORE the map fan-out
    spends a cent — the old failure mode billed EVERY map chunk, then
    merge() degraded the synthesis ChatError into raw concatenation and the
    user never saw the real 'unknown model' diagnosis. Clean [model] error
    with did-you-mean suggestions, prose AND --json envelope, exit 1."""
    _argv_command = deps._argv_command
    _fail_exit = deps._fail_exit
    difflib = deps.difflib
    ids = [m["id"] for m in catalog
           if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if not ids or chosen in ids:
        return
    close = difflib.get_close_matches(chosen, ids, n=3, cutoff=0.4)
    hint = f" — did you mean: {', '.join(close)}?" if close else ""
    msg = (f"unknown reduce model '{chosen}' (from {source}){hint} "
           "— nothing was run or billed. See: ambient-codex models")
    _fail_exit(args, _argv_command(), "model", msg,
               prose=f"ambient [model]: {msg}")


def resolve_reduce_model(args, conf, map_model, catalog=None, deps=None):
    """Model for the map-reduce SYNTHESIS step (cheap-map /
    strong-reduce): --reduce-model flag > AMBIENT_MODEL_MAP 'reduce' phase >
    the map model itself. An explicit concrete -m pins the WHOLE run — the
    config map must never reroute the synthesis behind the user's back.
    When a `catalog` is available: an `auto[:…]` reduce spec resolves to a
    concrete READY pick (printed) instead of being silently dropped, and a
    concrete id is VALIDATED up front so a typo'd --reduce-model / stale
    map entry fails before any map spend. A degraded/absent catalog
    changes nothing — no false refusals while offline."""
    _validate_reduce_id = deps._validate_reduce_id
    is_auto_model = deps.is_auto_model
    model_map = deps.model_map
    resolve_auto_model = deps.resolve_auto_model
    sys = deps.sys
    flag = getattr(args, "reduce_model", None)
    if flag:
        chosen, source = flag, "--reduce-model"
    else:
        explicit = getattr(args, "model", None)
        if explicit and not is_auto_model(explicit):
            return map_model
        mapped = model_map(conf).get("reduce")
        if not mapped:
            return map_model
        chosen, source = mapped, "AMBIENT_MODEL_MAP reduce="
    if is_auto_model(chosen):
        if catalog:
            # input_chars=0 is intentional: the reduce input (concatenated map
            # extracts) is genuinely unknown here, and run_map_reduce clamps the
            # hierarchical synthesis to the CHOSEN reduce model's own window
            # (multi-level reduce) so a small model degrades, never 400s.
            # auto:cheapest therefore honestly returns the cheapest READY model.
            return resolve_auto_model(chosen, catalog, conf, 0, args,
                                      label=source)
        # No catalog to resolve against: say so explicitly rather than
        # letting the pseudo-model id reach the synthesis call (or silently
        # vanishing, the old behavior for a mapped auto spec).
        print(f"ambient: note — {source} {chosen} ignored (model catalog "
              f"unavailable) — synthesis stays on {map_model}",
              file=sys.stderr)
        return map_model
    if chosen != map_model and catalog:
        _validate_reduce_id(chosen, catalog, args, source)
    return chosen


def response_format_for(profile, schema, deps=None):
    """Capability-gated structured-output request for a model (A4): strict
    json_schema when supported, else json_object, else None (prompt-only).
    Sending response_format to a model that lacks it can 400 — so gate."""
    _model_budget = deps._model_budget
    return _model_budget.response_format_for(profile, schema)


def _model_budget_constants(deps=None):
    """Return immutable-in-use constants for extracted model budget math."""
    ANSWER_TOKENS_RESERVE = deps.ANSWER_TOKENS_RESERVE
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    CONTEXT_OVERHEAD_TOKENS = deps.CONTEXT_OVERHEAD_TOKENS
    INPUT_TOKEN_SAFETY = deps.INPUT_TOKEN_SAFETY
    MIN_REASONING_CHUNK = deps.MIN_REASONING_CHUNK
    OUTPUT_SAFETY = deps.OUTPUT_SAFETY
    REASONING_EXPANSION = deps.REASONING_EXPANSION
    return {
        "CHARS_PER_TOKEN": CHARS_PER_TOKEN,
        "REASONING_EXPANSION": REASONING_EXPANSION,
        "ANSWER_TOKENS_RESERVE": ANSWER_TOKENS_RESERVE,
        "OUTPUT_SAFETY": OUTPUT_SAFETY,
        "INPUT_TOKEN_SAFETY": INPUT_TOKEN_SAFETY,
        "CONTEXT_OVERHEAD_TOKENS": CONTEXT_OVERHEAD_TOKENS,
        "MIN_REASONING_CHUNK": MIN_REASONING_CHUNK,
    }


def _model_profile_constants(deps=None):
    """Return the model-profile inputs without coupling the pure module to CLI state."""
    FALLBACK_CONTEXT = deps.FALLBACK_CONTEXT
    FALLBACK_MAX_OUTPUT = deps.FALLBACK_MAX_OUTPUT
    MAX_AUTO_BUDGET_TOKENS = deps.MAX_AUTO_BUDGET_TOKENS
    MIN_OUTPUT_TOKENS = deps.MIN_OUTPUT_TOKENS
    NONREASONING_CONTEXT_MARGIN = deps.NONREASONING_CONTEXT_MARGIN
    NONREASONING_OUTPUT_BUDGET = deps.NONREASONING_OUTPUT_BUDGET
    REASONING_CHUNK_FACTOR = deps.REASONING_CHUNK_FACTOR
    _model_budget_constants = deps._model_budget_constants
    return {
        **_model_budget_constants(),
        "FALLBACK_CONTEXT": FALLBACK_CONTEXT,
        "FALLBACK_MAX_OUTPUT": FALLBACK_MAX_OUTPUT,
        "MAX_AUTO_BUDGET_TOKENS": MAX_AUTO_BUDGET_TOKENS,
        "MIN_OUTPUT_TOKENS": MIN_OUTPUT_TOKENS,
        "NONREASONING_OUTPUT_BUDGET": NONREASONING_OUTPUT_BUDGET,
        "NONREASONING_CONTEXT_MARGIN": NONREASONING_CONTEXT_MARGIN,
        "REASONING_CHUNK_FACTOR": REASONING_CHUNK_FACTOR,
    }


def _reasoning_output_budget(input_chars, cpt=None, deps=None):
    """Tokens needed to REASON over `input_chars` of dense code AND still emit a
    real answer: reasoning_tokens + a fixed answer reserve, times a safety
    factor. `cpt` lets model_profile pass the model's OBSERVED chars-per-token
; default = the static constant, byte-identical."""
    _model_budget = deps._model_budget
    _model_budget_constants = deps._model_budget_constants
    return _model_budget.reasoning_output_budget(
        input_chars, cpt, _model_budget_constants())


def _context_safe_output_cap(profile, input_chars=None, cpt=None, deps=None):
    """Maximum output tokens that leave room for this input in context.

    The static 3.2 chars/token estimate is optimistic for guttered source code,
    so context checks use an extra input-token safety factor. The lower bound is
    a last-resort floor for tiny windows; callers still prefer smaller chunks
    when they can.
    """
    _model_budget = deps._model_budget
    _model_budget_constants = deps._model_budget_constants
    return _model_budget.context_safe_output_cap(
        profile, input_chars, cpt, _model_budget_constants())


def _context_safe_escalation_ceiling(profile, input_chars=None, cpt=None, deps=None):
    _model_budget = deps._model_budget
    _model_budget_constants = deps._model_budget_constants
    return _model_budget.context_safe_escalation_ceiling(
        profile, input_chars, cpt, _model_budget_constants())


def single_shot_max_chars(deps=None):
    """Cost-knob cap on how large a single-shot reasoning call may get (A1)."""
    REASONING_SINGLE_SHOT_CHARS = deps.REASONING_SINGLE_SHOT_CHARS
    SINGLE_SHOT_MAX_CHARS_DEFAULT = deps.SINGLE_SHOT_MAX_CHARS_DEFAULT
    os = deps.os
    raw = os.environ.get("AMBIENT_SINGLE_SHOT_MAX_CHARS")
    if raw:
        try:
            return max(REASONING_SINGLE_SHOT_CHARS, int(raw))
        except ValueError:
            pass
    return SINGLE_SHOT_MAX_CHARS_DEFAULT


def _reasoning_single_shot_target(ctx, max_out, cpt=None, deps=None):
    """Largest input a reasoning model can take in ONE pass and still fit both
    its output cap and its context window. Bounded by the cost knob. `cpt` =
    observed chars-per-token when history exists; default static."""
    _model_budget = deps._model_budget
    _model_budget_constants = deps._model_budget_constants
    single_shot_max_chars = deps.single_shot_max_chars
    return _model_budget.reasoning_single_shot_target(
        ctx, max_out, cpt, single_shot_max_chars(), _model_budget_constants())


def model_profile(catalog, model, deps=None):
    """Derive per-model tuning from /v1/models metadata (PURE — pass an already-
    fetched catalog). Reasoning models get a GENEROUS output budget so reasoning
    AND answer both fit, plus SMALL chunks so reasoning-per-chunk stays bounded;
    non-reasoning models get a modest budget and LARGE (context-sized) chunks.
    An unknown/offline model falls back to conservative REASONING defaults — the
    safer failure mode (over-budget, under-chunk). Char↔token conversion uses
    the model's OBSERVED chars-per-token when the local ledger has real usage
    history (AMBIENT_TELEMETRY=off opts out), else
    the static CHARS_PER_TOKEN — a no-history profile is byte-identical."""
    ModelProfile = deps.ModelProfile
    _as_pos_int = deps._as_pos_int
    _effective_cpt = deps._effective_cpt
    _model_profile_constants = deps._model_profile_constants
    _model_profiles = deps._model_profiles
    single_shot_max_chars = deps.single_shot_max_chars
    return _model_profiles.build_model_profile(
        catalog, model, _effective_cpt, _as_pos_int, ModelProfile,
        _model_profile_constants(), single_shot_max_chars(),
    )


def _resolve_output_budget(max_tokens, profile, input_chars=None, deps=None):
    """Pure core of the output-budget derivation: (max_tokens, auto_budget)
    for this profile. Shared by apply_output_budget (mutable Namespace, kept
    for the cmd_* boundary) and RequestSpec.with_output_budget (frozen engine
    spec) — ONE implementation so the two carriers can never
    drift. When `input_chars` is given, RIGHT-SIZE a reasoning model's budget
    to the actual input (A1) — so a small prompt gets a sane ceiling, not the
    worst-case one."""
    MIN_OUTPUT_TOKENS = deps.MIN_OUTPUT_TOKENS
    _effective_cpt = deps._effective_cpt
    _model_budget = deps._model_budget
    _model_budget_constants = deps._model_budget_constants
    sys = deps.sys
    return _model_budget.resolve_output_budget(
        max_tokens, profile, input_chars, _effective_cpt,
        _model_budget_constants(),
        lambda message: print(message, file=sys.stderr), MIN_OUTPUT_TOKENS,
    )


def apply_output_budget(args, profile, input_chars=None, deps=None):
    """Resolve args.max_tokens from the profile unless the user set it
    explicitly, and stash the escalation ceiling for complete(). Runs before
    any pricing estimate in every command, so estimates reflect the real budget. The
    cmd_*-boundary (mutable argparse.Namespace) twin of
    RequestSpec.with_output_budget — both delegate to _resolve_output_budget."""
    _context_safe_escalation_ceiling = deps._context_safe_escalation_ceiling
    _effective_cpt = deps._effective_cpt
    _resolve_output_budget = deps._resolve_output_budget
    args.max_tokens, args._auto_budget = _resolve_output_budget(
        getattr(args, "max_tokens", None), profile, input_chars)
    ceiling = _context_safe_escalation_ceiling(
        profile, input_chars, _effective_cpt(profile.model))
    args.escalation_ceiling = max(args.max_tokens, ceiling)
