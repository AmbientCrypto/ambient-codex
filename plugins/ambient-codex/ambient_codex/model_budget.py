"""Pure context and structured-output budget calculations for model profiles."""

import math


def response_format_for(profile, schema):
    """Return the strongest response-format capability advertised by a model."""
    features = profile.features or []
    if "structured_outputs" in features:
        return {"type": "json_schema", "json_schema": {
            "name": "ambient_findings", "strict": True, "schema": schema,
        }}
    if "json_mode" in features:
        return {"type": "json_object"}
    return None


def reasoning_output_budget(input_chars, chars_per_token, constants):
    """Return the output allowance needed for reasoning plus a real answer."""
    cpt = chars_per_token or constants["CHARS_PER_TOKEN"]
    reasoning_tokens = input_chars * constants["REASONING_EXPANSION"] / cpt
    return int((reasoning_tokens + constants["ANSWER_TOKENS_RESERVE"])
               * constants["OUTPUT_SAFETY"])


def context_safe_output_cap(profile, input_chars, chars_per_token, constants):
    """Cap output so a model's input, output, and framing fit its context."""
    cpt = chars_per_token or constants["CHARS_PER_TOKEN"]
    chars = profile.single_shot_chars if input_chars is None else input_chars
    input_tokens = math.ceil(max(0, chars) / cpt
                             * constants["INPUT_TOKEN_SAFETY"])
    return max(256, min(
        profile.max_output_length,
        profile.context_length - input_tokens - constants["CONTEXT_OVERHEAD_TOKENS"],
    ))


def context_safe_escalation_ceiling(profile, input_chars, chars_per_token,
                                    constants):
    """Cap a retry escalation by both model and remaining-context limits."""
    cap = context_safe_output_cap(profile, input_chars, chars_per_token, constants)
    return max(256, min(profile.escalation_ceiling, cap))


def reasoning_single_shot_target(context_length, max_output, chars_per_token,
                                 single_shot_cap, constants):
    """Return the largest safe one-pass reasoning input under all constraints."""
    cpt = chars_per_token or constants["CHARS_PER_TOKEN"]
    by_output = ((max_output / constants["OUTPUT_SAFETY"]
                  - constants["ANSWER_TOKENS_RESERVE"])
                 * cpt / constants["REASONING_EXPANSION"])
    denominator = ((1 + constants["REASONING_EXPANSION"]
                    * constants["OUTPUT_SAFETY"]) / cpt)
    by_context = ((context_length
                   - constants["ANSWER_TOKENS_RESERVE"]
                   * constants["OUTPUT_SAFETY"]
                   - constants["CONTEXT_OVERHEAD_TOKENS"]) / denominator)
    return int(max(constants["MIN_REASONING_CHUNK"],
                   min(by_output, by_context, single_shot_cap)))


def resolve_output_budget(max_tokens, profile, input_chars, effective_cpt,
                          constants, emit, minimum_output_tokens):
    """Resolve a requested output budget against a profile and actual input."""
    cpt = effective_cpt(profile.model)
    cap = context_safe_output_cap(profile, input_chars, cpt, constants)
    if max_tokens is None:
        if input_chars is not None and profile.is_reasoning:
            wanted = reasoning_output_budget(input_chars, cpt, constants)
            floor = min(8192, profile.output_budget)
            budget = max(floor, min(wanted, profile.output_budget))
            return max(256, min(budget, cap)), True
        return max(256, min(profile.output_budget, cap)), True
    floor = min(minimum_output_tokens, profile.max_output_length, cap)
    resolved = max(floor, min(max_tokens, profile.max_output_length, cap))
    if profile.is_reasoning:
        needed = (reasoning_output_budget(input_chars, cpt, constants)
                  if input_chars is not None else profile.output_budget)
        needed = min(needed, profile.output_budget)
        if resolved < needed:
            if max_tokens > cap:
                emit(
                    f"ambient: --max-tokens {max_tokens} was clamped to "
                    f"{resolved} so input+output fit the model context; if "
                    "the answer truncates, narrow the input or let Ambient "
                    "split it into smaller chunks."
                )
                return resolved, False
            emit(
                f"ambient: --max-tokens {resolved} may be low for a reasoning "
                f"model — it emits reasoning tokens before the answer and can "
                f"need ~{needed} to finish; it may stop mid-reasoning (the "
                "auto-escalation guard will try to recover)."
            )
    return resolved, False


__all__ = ("response_format_for", "reasoning_output_budget",
           "context_safe_output_cap", "context_safe_escalation_ceiling",
           "reasoning_single_shot_target", "resolve_output_budget")
