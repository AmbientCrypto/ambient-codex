"""Fallback-aware internal estimates and alternate-model selection."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class CostDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _lane_pricing(catalog, model, deps=None):
    """(pricing tuple, assumed?) — the worst-case substitution estimate_cost
    applies, exposed for the per-lane split estimate."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    model_pricing = deps.model_pricing
    price = model_pricing(catalog, model)
    if price is None:
        return (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE), True
    return price, False


def _fallback_enabled(spec, conf, deps=None):
    """Is the opt-in fallback lane live for this request? ONE derivation
    (--fallback flag OR AMBIENT_FALLBACK env/config, with the SACRED
    _no_fallback override) shared by complete()'s live swap and the
    fallback-aware up-front batch gates — the two can never disagree about
    whether a swap is possible."""
    os = deps.os
    if spec._no_fallback:
        return False  # SACRED: the model IS the choice — no swap, ever
    raw = (os.environ.get("AMBIENT_FALLBACK")
           or (conf or {}).get("AMBIENT_FALLBACK", ""))
    return bool(spec.fallback or str(raw).lower() in ("1", "on", "true"))


def _batch_fallback_alt(catalog, model, args, conf, per_call_input_chars, deps=None):
    """The ONE deterministic fallback candidate an UP-FRONT batch gate must
    ALSO price. Fan-out workers do not re-gate a
    live --fallback swap per call (see RequestSpec.gate_fallback) — instead
    the batch reserves the swap exposure up front. This returns the same
    fit-then-cheapest candidate pick_fallback_model would hand a live worker
    (sized to the per-call input; ONE hop — complete()'s fallback_retried
    flag means a call only ever swaps once), or None when fallback is off /
    SACRED-disabled / nothing fits. The candidate-hidden stderr note is
    suppressed: at gate time nothing has failed yet — a live swap still
    prints it."""
    RequestSpec = deps.RequestSpec
    _cost_cpt = deps._cost_cpt
    _fallback_enabled = deps._fallback_enabled
    contextlib = deps.contextlib
    io = deps.io
    pick_fallback_model = deps.pick_fallback_model
    spec = RequestSpec.from_args(args)
    if not _fallback_enabled(spec, conf):
        return None
    min_ctx = int(per_call_input_chars / _cost_cpt(model))
    with contextlib.redirect_stderr(io.StringIO()):
        return pick_fallback_model(catalog, model, min_context=min_ctx,
                                   conf=conf or {})


def _fb_call_cost(catalog, model, chars, max_tokens, in_factor,
                  cpt_model=None, deps=None):
    """(expected, bound, assumed) for ONE call of a batch — the per-call
    term of the fallback-aware SUM-OF-MAXIMA reserve. `in_factor` is the
    lane's share of the input billing (1.3 = estimate_cost's single-model
    batches; 1.0 map / 0.3 synthesis re-read / 0.0 output-only extra call =
    estimate_cost_mr's lane split), so summing the per-call terms over a
    lane reproduces the base estimate's own decomposition exactly.
    `cpt_model` (the MAP model in the synthesis lane) keeps the
    chars→tokens basis identical to that decomposition."""
    ASSUMED_MAX_INPUT_PRICE = deps.ASSUMED_MAX_INPUT_PRICE
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _cost_cpt = deps._cost_cpt
    _expected_output_tokens = deps._expected_output_tokens
    model_pricing = deps.model_pricing
    price = model_pricing(catalog, model)
    assumed = price is None
    if assumed:
        price = (ASSUMED_MAX_INPUT_PRICE, ASSUMED_MAX_OUTPUT_PRICE)
    in_cost = (chars / _cost_cpt(cpt_model or model)) * in_factor * price[0]
    bound = (in_cost + max_tokens * price[1]) / 1e6
    expected_out = _expected_output_tokens(catalog, model, max_tokens)
    expected = (in_cost + expected_out * price[1]) / 1e6
    return expected, bound, assumed


def _fb_alt_budget(catalog, alt, requested_max_tokens, auto_budget, deps=None):
    """The output budget a live worker RESENDS after swapping to `alt` —
    the exact alt_tokens re-derivation complete() performs (
    H1): auto-budget → the ALT's own profile budget (which can be far
    LARGER than a small-cap requested model's resolved max_tokens);
    explicit --max-tokens → that value clamped to the alt's real output
    cap. Pricing the alt at the requested model's budget under-reserved
    every auto-budget swap onto a bigger-budget candidate."""
    model_profile = deps.model_profile
    profile = model_profile(catalog, alt)
    if auto_budget:
        return profile.output_budget
    return min(requested_max_tokens, profile.max_output_length)


def estimate_cost_fb(catalog, model, input_chars, n_calls, max_tokens,
                     args, conf, per_call_chars=None, per_call_tokens=None, deps=None):
    """estimate_cost made FALLBACK-AWARE for the up-front batch gates and
    plans: with
    --fallback/AMBIENT_FALLBACK live, ANY SUBSET of the batch may legally
    be re-served by a fallback candidate, so the reserve is the PER-CALL
    SUM-OF-MAXIMA  Σ_i max(cost_requested(call_i), cost_alt(call_i)) —
    computed as base + Σ_i max(0, alt_i − requested_i), which dominates
    EVERY requested/fallback mixture (the old max(sum_requested, sum_alt)
    did not: uneven inputs + crossed price vectors let a mixture exceed
    both totals). Per call, the candidate is picked from THAT call's own
    input size (`per_call_chars` — map's uneven items would otherwise hide
    a big item's pricier large-context candidate behind the batch average)
    and priced at the ALT's OWN resolved output budget exactly as live
    complete() re-derives it (`per_call_tokens` carries per-item budgets;
    see _fb_alt_budget). PARITY: fallback off / SACRED _no_fallback / no
    pricier candidate → the figures are byte-identical to estimate_cost.
    Never under-counts spend."""
    RequestSpec = deps.RequestSpec
    _batch_fallback_alt = deps._batch_fallback_alt
    _fallback_enabled = deps._fallback_enabled
    _fb_alt_budget = deps._fb_alt_budget
    _fb_call_cost = deps._fb_call_cost
    estimate_cost = deps.estimate_cost
    sys = deps.sys
    exp, bnd, asm = estimate_cost(catalog, model, input_chars, n_calls,
                                  max_tokens)
    spec = RequestSpec.from_args(args)
    if n_calls < 1 or not _fallback_enabled(spec, conf):
        return exp, bnd, asm
    if per_call_chars is None:  # uneven lanes may thread these on args
        per_call_chars = getattr(args, "_fb_per_call_chars", None)
    if per_call_tokens is None:
        per_call_tokens = getattr(args, "_fb_per_call_tokens", None)
    uniform = per_call_chars is None and per_call_tokens is None
    # L9: a PROVIDED per-call vector whose length != n_calls is an internal
    # inconsistency. zip() would silently truncate the pricing loop and
    # UNDER-reserve — but there is also NO safe conservative re-price: the
    # fallback uplift is not monotone in size (a larger synthetic size can
    # select a different, cheaper-uplift candidate), so an omitted large item
    # can't be bounded by any single synthetic size. Degrade to the base
    # requested-model estimate (which fully gates the REAL spend; only the
    # secondary --fallback uplift is skipped) and warn, rather than mis-reserve.
    if (per_call_chars is not None and len(per_call_chars) != n_calls) or \
            (per_call_tokens is not None and len(per_call_tokens) != n_calls):
        print("ambient: per-call pricing vector length mismatch — using the "
              "base estimate without the fallback uplift", file=sys.stderr)
        return exp, bnd, asm
    sizes = (list(per_call_chars) if per_call_chars is not None
             else [input_chars / n_calls] * n_calls)
    budgets = (list(per_call_tokens) if per_call_tokens is not None
               else [max_tokens] * n_calls)
    d_exp = d_bnd = 0.0
    alt_assumed = False
    memo = {}  # (chars, budget) → per-call uplift; uniform lanes hit once
    for chars_i, mt_i in zip(sizes, budgets):
        cell = memo.get((chars_i, mt_i))
        if cell is None:
            alt = _batch_fallback_alt(catalog, model, args, conf, chars_i)
            if alt is None:
                cell = (0.0, 0.0, False, None, None)
            else:
                alt_mt = _fb_alt_budget(catalog, alt, mt_i,
                                        spec._auto_budget)
                r_exp, r_bnd, _r_asm = _fb_call_cost(
                    catalog, model, chars_i, mt_i, 1.3)
                a_exp, a_bnd, a_asm = _fb_call_cost(
                    catalog, alt, chars_i, alt_mt, 1.3)
                cell = (max(0.0, a_exp - r_exp), max(0.0, a_bnd - r_bnd),
                        a_asm, alt, alt_mt)
            memo[(chars_i, mt_i)] = cell
        de, db, a_asm, _alt, _alt_mt = cell
        if de > 0.0 or db > 0.0:
            d_exp += de
            d_bnd += db
            alt_assumed = alt_assumed or a_asm
    if d_exp <= 0.0 and d_bnd <= 0.0:
        return exp, bnd, asm  # no pricier candidate — byte-identical parity
    exp_fb, bnd_fb = exp + d_exp, bnd + d_bnd
    if uniform:
        # A uniform batch's sum-of-maxima equals the all-alt total
        # analytically when the candidate is pricier on every component —
        # floor at that exact figure so float association can never leave
        # the reserve an ulp under it (the fleet record pins exactness).
        _de, _db, _asm2, alt, alt_mt = memo[(sizes[0], budgets[0])]
        if alt is not None:
            f_exp, f_bnd, _f_asm = estimate_cost(catalog, alt, input_chars,
                                                 n_calls, alt_mt)
            exp_fb = max(exp_fb, f_exp)
            bnd_fb = max(bnd_fb, f_bnd)
    return exp_fb, bnd_fb, asm or alt_assumed


def estimate_cost_mr_fb(catalog, model, reduce_model, input_chars, n_chunks,
                        max_tokens, args, conf, extra_calls=0,
                        synthesis=True, per_call_chars=None, deps=None):
    """estimate_cost_mr made FALLBACK-AWARE (same SUM-OF-MAXIMA contract as
    estimate_cost_fb): each LANE's calls may independently swap to that
    lane's own fallback candidate, so each lane adds its per-call
    max(0, alt − requested) uplift — map chunks at the full 1.0× per-call
    input, extra calls output-only (the base bills them no input), the
    synthesis re-read at its 0.3× share — and the alt is priced at ITS OWN
    resolved output budget (_fb_alt_budget). Each map/synthesis call's
    candidate is picked from THAT call's REAL chunk size (`per_call_chars`
    / args._fb_per_call_chars — final spend-safety HIGH: uneven chunks
    would otherwise hide a big chunk's pricier large-context candidate
    behind the input/n_chunks average; callers holding the packed chunk
    list thread it, exactly like cmd_map's uneven items). Dominates every
    mixture of requested-success and fallback across both lanes;
    byte-identical to estimate_cost_mr when fallback is off / SACRED / no
    candidate is pricier."""
    RequestSpec = deps.RequestSpec
    _batch_fallback_alt = deps._batch_fallback_alt
    _fallback_enabled = deps._fallback_enabled
    _fb_alt_budget = deps._fb_alt_budget
    _fb_call_cost = deps._fb_call_cost
    estimate_cost_mr = deps.estimate_cost_mr
    rmodel = reduce_model or model
    base = estimate_cost_mr(catalog, model, rmodel, input_chars, n_chunks,
                            max_tokens, extra_calls=extra_calls,
                            synthesis=synthesis)
    spec = RequestSpec.from_args(args)
    if not _fallback_enabled(spec, conf):
        return base
    if per_call_chars is None:  # uneven lanes may thread these on args
        per_call_chars = getattr(args, "_fb_per_call_chars", None)
    per_call = input_chars / max(1, n_chunks)
    sizes = (list(per_call_chars) if per_call_chars
             else [per_call] * n_chunks)

    alts = {}  # (lane model, per-call chars) → (candidate, its own budget)

    def lane_alt(lane_model, chars_i):
        cell = alts.get((lane_model, chars_i))
        if cell is None:
            alt = _batch_fallback_alt(catalog, lane_model, args, conf,
                                      chars_i)
            cell = (alt, None if alt is None else _fb_alt_budget(
                catalog, alt, max_tokens, spec._auto_budget))
            alts[(lane_model, chars_i)] = cell
        return cell

    def call_uplift(lane_model, chars_i, in_factor):
        """(d_exp, d_bnd, assumed) ONE lane call adds — zeros when its
        candidate is missing or cheaper-or-equal (parity)."""
        alt, alt_mt = lane_alt(lane_model, chars_i)
        if alt is None:
            return 0.0, 0.0, False
        r_exp, r_bnd, _r_asm = _fb_call_cost(
            catalog, lane_model, chars_i, max_tokens, in_factor,
            cpt_model=model)  # base tokenizes ALL input at the map model
        a_exp, a_bnd, a_asm = _fb_call_cost(catalog, alt, chars_i, alt_mt,
                                            in_factor)
        de = max(0.0, a_exp - r_exp)
        db = max(0.0, a_bnd - r_bnd)
        if de <= 0.0 and db <= 0.0:
            return 0.0, 0.0, False
        return de, db, a_asm

    def lane_uplift(lane_model, lane_sizes, in_factor):
        """One lane's total uplift, grouped by size — a uniform lane is ONE
        per-call figure times its count, float-identical to the pre-
        per-chunk uniform math."""
        counts = {}
        for c in lane_sizes:
            counts[c] = counts.get(c, 0) + 1
        d_exp = d_bnd = 0.0
        asm = False
        for c, n in counts.items():
            de, db, a = call_uplift(lane_model, c, in_factor)
            d_exp += de * n
            d_bnd += db * n
            asm = asm or a
        return d_exp, d_bnd, asm

    # Extra calls are output-only in the base; their candidate is picked at
    # the LARGEST chunk (conservative: the context-fit constraint that
    # forces a pricier large-window candidate only tightens with size).
    top = max(sizes, default=per_call)
    lanes = [lane_uplift(model, sizes, 1.0),
             lane_uplift(model, [top] * extra_calls, 0.0)]
    if synthesis:
        lanes.append(lane_uplift(rmodel, sizes, 0.3))
    d_exp = sum(lane[0] for lane in lanes)
    d_bnd = sum(lane[1] for lane in lanes)
    if d_exp <= 0.0 and d_bnd <= 0.0:
        return base  # no pricier candidate — byte-identical parity
    exp_fb, bnd_fb = base[0] + d_exp, base[1] + d_bnd

    def sole_alt(lane_model):
        """The lane's single distinct candidate, or None when its chunks
        resolved DIFFERENT candidates — the per-call sum-of-maxima already
        dominates every mixture then; no uniform all-alt total exists."""
        cand = {alts.get((lane_model, c), (None, None))[0] for c in sizes}
        cand.discard(None)
        return cand.pop() if len(cand) == 1 else None

    # Floor at the exact all-alt lane-combination totals (the uniform
    # analogue of estimate_cost_fb's floor): the uplift sum equals the
    # worst combination analytically — never let float association leave
    # the reserve an ulp under it.
    alt_m = sole_alt(model)
    alt_r = sole_alt(rmodel) if synthesis else None
    map_models = [model] + ([alt_m] if alt_m else [])
    red_models = [rmodel] + ([alt_r] if synthesis and alt_r else [])
    for m in map_models:
        for r in red_models:
            if (m, r) == (model, rmodel):
                continue
            c = estimate_cost_mr(catalog, m, r, input_chars, n_chunks,
                                 max_tokens, extra_calls=extra_calls,
                                 synthesis=synthesis)
            exp_fb = max(exp_fb, c[0])
            bnd_fb = max(bnd_fb, c[1])
    return exp_fb, bnd_fb, base[2] or any(lane[2] for lane in lanes)


def estimate_cost_mr(catalog, model, reduce_model, input_chars, n_chunks,
                     max_tokens, extra_calls=0, synthesis=True, deps=None):
    """(expected, bound, assumed) for a map-reduce run, priced PER LANE:
    map input ~1.0x at map prices + synthesis input ~0.3x (the partials the
    reduce step re-reads) at REDUCE prices + each lane's own output calls at
    its own price. Reusing the 1.3x single-model helper for both lanes
    double-counted the synthesis re-read. When map == reduce this
    is byte-identical to estimate_cost(n_chunks*2 + extra_calls). With
    synthesis=False (deterministic reducer — findings_reducer does a pure
    Python merge) there is NO synthesis LLM call at all: only the map lane
    is priced, input billed once."""
    _cost_cpt = deps._cost_cpt
    _expected_output_tokens = deps._expected_output_tokens
    _lane_pricing = deps._lane_pricing
    estimate_cost = deps.estimate_cost
    if not synthesis:
        price, assumed = _lane_pricing(catalog, model)
        in_tok = input_chars / _cost_cpt(model)
        n_calls = n_chunks + extra_calls
        input_cost = in_tok * price[0]
        bound = (input_cost + n_calls * max_tokens * price[1]) / 1e6
        expected_out = _expected_output_tokens(catalog, model, max_tokens)
        expected = (input_cost + n_calls * expected_out * price[1]) / 1e6
        return expected, bound, assumed
    rmodel = reduce_model or model
    if rmodel == model:
        # Delegate so the number stays BYTE-identical to the classic gate.
        return estimate_cost(catalog, model, input_chars,
                             n_chunks * 2 + extra_calls, max_tokens)
    mp, asm_m = _lane_pricing(catalog, model)
    rp, asm_r = _lane_pricing(catalog, rmodel)
    in_tok = input_chars / _cost_cpt(model)  # the input is MAP-side data
    input_cost = in_tok * 1.0 * mp[0] + in_tok * 0.3 * rp[0]
    map_calls, red_calls = n_chunks + extra_calls, n_chunks
    bound = (input_cost + map_calls * max_tokens * mp[1]
             + red_calls * max_tokens * rp[1]) / 1e6
    map_expected_out = _expected_output_tokens(catalog, model, max_tokens)
    reduce_expected_out = _expected_output_tokens(
        catalog, rmodel, max_tokens
    )
    expected = (input_cost + map_calls * map_expected_out * mp[1]
                + red_calls * reduce_expected_out * rp[1]) / 1e6
    return expected, bound, asm_m or asm_r


def pick_fallback_model(catalog, current, min_context=0, conf=None, deps=None):
    """Choose a READY replacement that can actually do chat work — text output,
    not an embedding/vision-only model — so opt-in fallback never silently
    switches to a model that would produce nonsense. Requires context_length >=
    min_context so the alt can hold the chunk that was already sized for the
    original model (a non-reasoner's huge chunk must not land on a
    small-window model). Curation-aware: never auto-switch INTO a model the
    user curated out while any visible candidate exists, prefer the user's
    other configured lane default, then the CHEAPEST fitting model by output
    per Mtok, tie-broken by bigger context (fit-then-cheapest
    the old biggest-context-first ranking paid frontier prices for headroom
    the chunk never needed). Returns None if none fit."""
    ASSUMED_MAX_OUTPUT_PRICE = deps.ASSUMED_MAX_OUTPUT_PRICE
    _as_bool = deps._as_bool
    _as_pos_int = deps._as_pos_int
    argparse = deps.argparse
    curation = deps.curation
    is_hidden = deps.is_hidden
    model_pricing = deps.model_pricing
    resolve_model = deps.resolve_model
    sys = deps.sys
    conf = conf if conf is not None else {}
    allow, hide, show, _notes = curation(conf)
    ns = argparse.Namespace(model=None)
    lane_defaults = {resolve_model(ns, conf, "chat"),
                     resolve_model(ns, conf, "code")}
    candidates = []
    skipped_hidden = 0
    for m in catalog:
        if not isinstance(m, dict) or not _as_bool(m.get("is_ready")) \
                or not m.get("id") or m.get("id") == current:
            continue
        out = m.get("output_modalities") or ["text"]
        if not isinstance(out, list) or "text" not in out:
            continue
        ctx = _as_pos_int(m.get("context_length"), 0)
        if ctx < min_context:
            continue
        mid = m["id"]
        hidden = is_hidden(mid, allow, hide, show)
        price = model_pricing(catalog, mid)
        out_price = price[1] if price else ASSUMED_MAX_OUTPUT_PRICE
        candidates.append(
            (1 if hidden else 0, 0 if mid in lane_defaults else 1,
             out_price, -ctx, mid))
        skipped_hidden += 1 if hidden else 0
    if not candidates:
        return None
    candidates.sort()
    best = candidates[0]
    if best[0] == 1:  # only curated-out models remain — don't auto-switch
        print(
            f"ambient: fallback — {skipped_hidden} READY model(s) skipped "
            "because your curation hides them",
            file=sys.stderr,
        )
        return None
    return best[4]
