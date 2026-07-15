"""Bounded completion retry, salvage, escalation, and fallback state machine."""

import dataclasses
import json
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class CompletionDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def run_completion(api_key, api_url, model, messages, args,
                   _stall_retried=False, _budget_retried=False,
                   _fallback_retried=False, _budget_shrunk=False,
                   on_delta=None, session=None, deps=None):
    """One completion. Returns (content, usage, body). Raises ChatError with a
    classified diagnosis, or NetworkError. Thread-safe (used by chunk fan-out).
    Degrades gracefully WITHOUT changing the user's chosen model: budget
    escalation -> labeled best-effort reasoning draft -> diagnosed error
    (callers may then split the work across the SAME model). The stall-retry,
    budget-escalation, and (opt-in) fallback guards are INDEPENDENT so one
    firing never disables another. The retry/fallback ladder is an
    explicit bounded loop over an immutable AttemptState (formerly recursive
    `return complete(...)` frames) — each guard computes the NEXT attempt via
    dataclasses.replace; observable behavior is identical. The
    request knobs ride a frozen RequestSpec (args is normalized at this
    boundary) — every retry variant is a dataclasses.replace of the spec,
    never a mutated/copied argparse.Namespace."""
    AttemptState = deps.AttemptState
    CHARS_PER_TOKEN = deps.CHARS_PER_TOKEN
    ChatError = deps.ChatError
    DEFAULT_BUDGET_ESCALATIONS = deps.DEFAULT_BUDGET_ESCALATIONS
    MAX_COMPLETE_ATTEMPTS = deps.MAX_COMPLETE_ATTEMPTS
    MIN_OUTPUT_TOKENS = deps.MIN_OUTPUT_TOKENS
    NetworkError = deps.NetworkError
    RequestSpec = deps.RequestSpec
    StallError = deps.StallError
    _as_pos_int = deps._as_pos_int
    _budget_escalation_limit = deps._budget_escalation_limit
    _effective_cpt = deps._effective_cpt
    _fallback_enabled = deps._fallback_enabled
    _reasoning_str = deps._reasoning_str
    _session_or = deps._session_or
    classify_error = deps.classify_error
    fetch_models = deps.fetch_models
    log_usage = deps.log_usage
    model_profile = deps.model_profile
    pick_fallback_model = deps.pick_fallback_model
    read_config_file = deps.read_config_file
    redact = deps.redact
    stream_completion = deps.stream_completion
    session = _session_or(session, api_key, api_url)
    api_key, api_url = session.api_key, session.api_url
    state = AttemptState(
        model=model, messages=messages, spec=RequestSpec.from_args(args),
        stall_retried=_stall_retried,
        budget_escalations=int(bool(_budget_retried)),
        fallback_retried=_fallback_retried, budget_shrunk=_budget_shrunk)
    budget_limit = _budget_escalation_limit(state.spec.max_budget_escalations)
    max_attempts = MAX_COMPLETE_ATTEMPTS + (
        budget_limit - DEFAULT_BUDGET_ESCALATIONS)
    for _ in range(max_attempts):
        model, messages, spec = state.model, state.messages, state.spec
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": spec.max_tokens,
            "temperature": spec.temperature,
        }
        rf = spec.response_format  # A4: structured output
        if rf:
            payload["response_format"] = rf
        # None transport = resolve the module global at CALL time (tests
        # monkeypatch `stream_completion`; an injected Session transport wins).
        send = (session.transport if session.transport is not None
                else stream_completion)
        try:
            # Stream to stdout only on the FIRST attempt — a stall-retry/
            # escalation restart would double-print the tail.
            status, body = send(
                api_url, api_key, payload, spec.timeout,
                on_delta=(on_delta if state.attempt_no == 0
                          and not state.stall_retried else None))
        except StallError as err:
            # A DATA-FLOW silence may be a transient upstream hiccup → retry once fresh. A
            # HARD-WALL hit means the generation is simply too long to finish
            # → do NOT restart from scratch (it would re-bill and re-hit the
            # wall); salvage immediately.
            if not err.hard_wall and not state.stall_retried:
                print(
                    f"ambient: {err} — retrying once on a fresh connection "
                    "(same model)",
                    file=sys.stderr,
                )
                state = dataclasses.replace(
                    state, stall_retried=True,
                    attempt_no=state.attempt_no + 1)
                continue
            if len(err.partial) > 400:
                why = "hit the time wall" if err.hard_wall else "stalled"
                print(
                    f"ambient: generation {why} — salvaging the partial output "
                    "already generated (marked incomplete)",
                    file=sys.stderr,
                )
                salvaged = (
                    "[AMBIENT NOTE: the stream ended mid-generation; this output "
                    "is PARTIAL — the tail is missing.]\n\n" + err.partial
                )
                # The partial output was PAID FOR: meter it as an estimated
                # record instead of returning empty usage — spend must never
                # under-count. Same char-based estimate as the
                # missing-usage path below; reasoning counts as output.
                in_tok = int(sum(len(m.get("content") or "") for m in messages)
                             / CHARS_PER_TOKEN)
                out_tok = int((len(err.partial) + len(err.reasoning or ""))
                              / CHARS_PER_TOKEN)
                usage = {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                         "_estimated": True}
                log_usage(model, usage)
                # `model` here is THIS attempt's model — after a --fallback
                # switch that IS the serving model, so the receipt prices
                # correctly.
                return salvaged, usage, {"salvaged_partial": True,
                                         "usage": usage, "_served_model": model}
            raise ChatError(
                "stall",
                f"'{model}' produced almost no output before stalling ({err}) "
                "— retry shortly, or pick another serving model: "
                "ambient-codex models",
            )
        if status != 200:
            category, diagnosis = classify_error(status, body, api_key)
            if category == "budget" and not state.budget_shrunk \
                    and spec.max_tokens > MIN_OUTPUT_TOKENS:
                # The server rejected our output budget outright (offline-
                # profile guess above the model's real cap). Splitting the
                # INPUT can't fix that — every chunk would resend the same
                # doomed max_tokens — so self-heal by halving the budget once.
                shrunk_tokens = max(MIN_OUTPUT_TOKENS, spec.max_tokens // 2)
                print(
                    f"ambient: server rejected the {spec.max_tokens}-token "
                    f"output budget — retrying once at {shrunk_tokens}",
                    file=sys.stderr,
                )
                state = dataclasses.replace(
                    state,
                    spec=dataclasses.replace(spec, max_tokens=shrunk_tokens),
                    budget_shrunk=True, attempt_no=state.attempt_no + 1)
                continue
            if category == "model":
                # Enriching the diagnosis / choosing a fallback must never
                # itself abort the call (esp. inside a map-reduce worker
                # thread) if the models endpoint is also unhealthy — degrade
                # gracefully.
                try:
                    catalog = fetch_models(api_url, api_key)
                except (NetworkError, SystemExit):
                    catalog = []
                # L8: prefer the session's already-loaded conf (honors any
                # session-level overrides + skips a disk re-read in this retry
                # path); fall back to reading the file only if none was threaded.
                fb_conf = session.conf or read_config_file()
                # SACRED override: callers running a model the user
                # EXPLICITLY chose (a --consensus set member, a chat /model
                # pick) set _no_fallback — the swap is disabled outright, even
                # when --fallback/AMBIENT_FALLBACK is on. The model IS the
                # choice; it fails as itself instead of silently becoming
                # another model.
                fallback_on = _fallback_enabled(spec, fb_conf)
                if fallback_on and not state.fallback_retried:
                    # The chunk was already sized for `model`; only fall back
                    # to a model whose context can hold the input we're about
                    # to resend PLUS its own output budget + overhead, so the
                    # switch can't overflow context or induce a marathon.
                    input_chars_sum = sum(len(m.get("content") or "")
                                          for m in messages)
                    input_tok = int(input_chars_sum / CHARS_PER_TOKEN)
                    alt = pick_fallback_model(catalog, model,
                                              min_context=input_tok,
                                              conf=fb_conf)
                    alt_profile = model_profile(catalog, alt) if alt else None
                    if alt_profile:
                        alt_budget = (alt_profile.output_budget
                                      if spec._auto_budget
                                      else min(spec.max_tokens,
                                               alt_profile.max_output_length))
                        # L3: size the input against the ALT model's OWN observed
                        # chars-per-token for the fit check — the global constant
                        # can mis-size a model with a different tokenizer and let
                        # a too-tight switch overflow context.
                        alt_input_tok = int(input_chars_sum / _effective_cpt(alt))
                        if alt_input_tok + alt_budget + 2500 \
                                > alt_profile.context_length:
                            alt = None  # can't hold input+output — don't switch
                    if alt and alt_profile:
                        print(
                            f"ambient: '{model}' isn't serving right now — "
                            f"using '{alt}' as you allowed (--fallback)",
                            file=sys.stderr,
                        )
                        if spec._auto_budget:
                            alt_tokens = alt_profile.output_budget
                        else:
                            alt_tokens = min(spec.max_tokens,
                                             alt_profile.max_output_length)
                        # Re-gate structured output for the fallback model —
                        # sending a strict json_schema to a model that lacks
                        # it 400s.
                        alt_rf = spec.response_format
                        if alt_rf:
                            afeats = alt_profile.features or []
                            if "structured_outputs" in afeats:
                                pass  # keep the strict schema
                            elif "json_mode" in afeats:
                                alt_rf = {"type": "json_object"}
                            else:
                                alt_rf = None
                        state = dataclasses.replace(
                            state, model=alt,
                            spec=dataclasses.replace(
                                spec, max_tokens=alt_tokens,
                                response_format=alt_rf,
                                escalation_ceiling=(
                                    alt_profile.escalation_ceiling)),
                            fallback_retried=True,
                            attempt_no=state.attempt_no + 1)
                        continue
                # NOTE: the serving-model list is intentionally NOT appended
                # here — for a concrete non-serving model the pre-flight advisory
                # (route_model) already printed it with prices, so repeating it
                # in the error read as the same thing twice. The message still
                # points at `ambient models`.
            err_obj = ChatError(category, diagnosis)
            # body may be a valid non-object JSON (str/list/number) on an
            # error response — guard before .get so it never AttributeErrors
            # to.
            if isinstance(body, dict) and body.get("_retry_after"):
                err_obj.retry_after = body["_retry_after"]
            raise err_obj
        if not isinstance(body, dict):
            # A 200 with a non-object JSON body (scalar/list/str) — `"content"
            # in body` would crash on an int. Treat as an unexpected shape.
            raise ChatError(
                "unknown",
                "unexpected response shape: "
                f"{redact(json.dumps(body)[:800], api_key)}")
        if "content" in body and "choices" not in body:
            # Streamed result (normal path).
            content = body.get("content")
            reasoning_text = _reasoning_str(body.get("reasoning"))
            finish = body.get("finish_reason")
        else:
            # Classic JSON body (endpoint ignored streaming).
            try:
                choice = body["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError):
                raise ChatError(
                    "unknown",
                    "unexpected response shape: "
                    f"{redact(json.dumps(body)[:800], api_key)}",
                )
            if not isinstance(choice, dict) or not isinstance(message, dict):
                raise ChatError(   # a non-object message → message.get would crash
                    "unknown",
                    "unexpected response shape: "
                    f"{redact(json.dumps(body)[:800], api_key)}")
            content = message.get("content")
            reasoning_text = _reasoning_str(message.get("reasoning_content"),
                                            message.get("reasoning"))
            finish = choice.get("finish_reason")
            # Surface finish_reason at the top level so every caller's
            # truncation check works on both the streamed and classic body
            # shapes.
            body.setdefault("finish_reason", finish)
        usage = body.get("usage")
        if not isinstance(usage, dict):   # a non-object usage → dict(usage) crash
            usage = {}
        content_str = content if isinstance(content, str) else ""
        # M19/L22: meter whenever we got content OR reasoning. A reasoning-only
        # response has content=None but still SPENT output tokens — the old
        # `isinstance(content, str)` gate skipped its metering entirely, so that
        # billed reasoning never reached the ledger / --json usage.
        if content_str or reasoning_text:
            # Ambient's stream does not reliably send a COMPLETE usage object
            # (probe finding — sometimes none, sometimes completion_tokens with
            # prompt_tokens=0), so metering + the --json envelope went blind on
            # the input. Fill any MISSING/zero field from char-based estimates
            # (reasoning counts as output); mark _estimated only when we had to
            # fill one, so a genuinely-complete usage stays exact and telemetry
            # ignores it. Far better than recording/emitting a 0.
            filled = dict(usage)
            if not _as_pos_int(usage.get("prompt_tokens"), 0):
                in_chars = sum(len(m.get("content") or "") for m in messages)
                if in_chars > 0:  # min 1: a non-empty prompt is never 0 tokens
                    filled["prompt_tokens"] = max(
                        1, int(in_chars / CHARS_PER_TOKEN))
                    filled["_estimated"] = True
            if not _as_pos_int(usage.get("completion_tokens"), 0):
                out_chars = len(content_str) + len(reasoning_text)
                if out_chars > 0:
                    filled["completion_tokens"] = max(
                        1, int(out_chars / CHARS_PER_TOKEN))
                    filled["_estimated"] = True
            usage = filled
            # Reflect it in the body too — ask/code --json read body["usage"]
            # and must never say null / 0 while the ledger has a real record.
            if isinstance(body, dict):
                body["usage"] = usage
        if usage:
            # Real usage carries the observed input char count for the Phase
            # 8a telemetry EWMA (estimated usage is ignored inside log_usage).
            log_usage(model, usage,
                      input_chars=sum(len(m.get("content") or "")
                                      for m in messages))
        if not isinstance(content, str) or not content.strip():
            # All budget spent on reasoning, nothing emitted: escalate ONCE,
            # bounded. The ceiling is the MODEL's real max output (from its
            # profile), not a flat 65536 — and we now start from a well-sized
            # budget, so bump gently.
            ceiling = spec.escalation_ceiling
            if (state.budget_escalations < budget_limit
                    and spec.max_tokens < ceiling):
                new_budget = min(max(spec.max_tokens * 2,
                                     spec.max_tokens + 16384),
                                 ceiling)
                print(
                    f"ambient: model spent all {spec.max_tokens} tokens "
                    f"reasoning with no final answer — retrying once with a "
                    f"{new_budget}-token budget (input is re-billed)",
                    file=sys.stderr,
                )
                state = dataclasses.replace(
                    state,
                    spec=dataclasses.replace(spec, max_tokens=new_budget),
                    budget_escalations=state.budget_escalations + 1,
                    attempt_no=state.attempt_no + 1)
                continue
            reasoning = reasoning_text
            input_chars = sum(len(m.get("content") or "") for m in messages)
            if len(reasoning) > 200 and input_chars < 20_000:
                # Small input, so splitting can't help: the thinking exists
                # even though the answer doesn't — a labeled draft beats an
                # empty hand.
                print(
                    "ambient: no final answer — returning the model's "
                    "reasoning as a labeled best-effort draft",
                    file=sys.stderr,
                )
                draft = (
                    "[AMBIENT NOTE: the model emitted reasoning but no final "
                    "answer. This is a best-effort draft assembled from that "
                    "reasoning — treat with extra skepticism.]\n\n" + reasoning
                )
                # reasoning_draft flags that `draft` is the model's THINKING, not
                # its output — consumers that parse structured output (e.g. the
                # build lane's JSONL records) must NOT mine it for real content.
                return draft, usage, {"salvaged_partial": True,
                                      "reasoning_draft": True,
                                      "usage": usage, "_served_model": model}
            raise ChatError(
                "empty",
                f"'{model}' produced no final content even after budget "
                f"escalation (finish_reason={finish}).",
            )
        if finish == "length":
            print(
                f"ambient: output hit the {spec.max_tokens}-token cap and may "
                "be truncated — rerun with a larger --max-tokens if it ends "
                "abruptly",
                file=sys.stderr,
            )
        # Record which model ACTUALLY served this result: an opt-in --fallback
        # switch happens in a later attempt, and a --json consumer must not be
        # told the content came from a model that wasn't serving.
        if isinstance(body, dict):
            body.setdefault("_served_model", model)
        return content.strip(), usage, body
    # Unreachable by construction: every `continue` above flips one guard
    # flag False→True and every guard requires its flag to be False, so the
    # ladder can take at most 4 retries after the initial attempt. The bound
    # exists so a future edit can never turn the loop into an unbounded spin.
    raise ChatError(
        "internal",
        f"completion retry ladder exceeded {max_attempts} attempts",
    )
