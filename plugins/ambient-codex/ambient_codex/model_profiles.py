"""Pure construction of live-catalog model execution profiles."""

from ambient_codex import model_budget


def build_model_profile(catalog, model, effective_cpt, as_pos_int, profile_type,
                        constants, single_shot_cap):
    """Derive a conservative ``ModelProfile`` from untrusted catalog metadata."""
    cpt = effective_cpt(model)
    metadata = next((entry for entry in (catalog or [])
                     if isinstance(entry, dict) and entry.get("id") == model), None)
    if metadata is None:
        context, max_output, features = (
            constants["FALLBACK_CONTEXT"], constants["FALLBACK_MAX_OUTPUT"],
            ["reasoning"],
        )
    else:
        context = as_pos_int(metadata.get("context_length"),
                             constants["FALLBACK_CONTEXT"])
        max_output = as_pos_int(metadata.get("max_output_length"),
                                constants["FALLBACK_MAX_OUTPUT"])
        raw_features = metadata.get("supported_features")
        features = [feature for feature in raw_features if isinstance(feature, str)] \
            if isinstance(raw_features, list) else []
    context = max(context, 4000)
    is_reasoning = "reasoning" in features
    ceiling = min(max_output, constants["MAX_AUTO_BUDGET_TOKENS"])
    if is_reasoning:
        single = model_budget.reasoning_single_shot_target(
            context, max_output, cpt, single_shot_cap, constants)
        output = model_budget.reasoning_output_budget(single, cpt, constants)
        if output > ceiling:
            output = ceiling
            reasoning_tokens = max(
                256, ceiling / constants["OUTPUT_SAFETY"]
                - constants["ANSWER_TOKENS_RESERVE"],
            )
            single = max(
                constants["MIN_REASONING_CHUNK"],
                int(reasoning_tokens * cpt / constants["REASONING_EXPANSION"]),
            )
        chunk = int(single * constants["REASONING_CHUNK_FACTOR"])
    else:
        output = min(
            constants["NONREASONING_OUTPUT_BUDGET"], max_output,
            max(constants["MIN_OUTPUT_TOKENS"], context // 2),
        )
        input_tokens = int(context * constants["NONREASONING_CONTEXT_MARGIN"]) - output
        input_tokens = max(1000, min(input_tokens, context - output - 1000))
        single = int(input_tokens * cpt)
        chunk = int(single * constants["REASONING_CHUNK_FACTOR"])
    if is_reasoning:
        while (single > 500
               and model_budget.reasoning_output_budget(single, cpt, constants) > output):
            single = int(single * 0.85)
        chunk = min(chunk, single)
    overhead = constants["CONTEXT_OVERHEAD_TOKENS"]
    max_input_tokens = context - output - overhead
    if max_input_tokens < 1000:
        output = min(max_output, max(256, context - 1000 - overhead))
        max_input_tokens = context - output - overhead
    single_tokens = -(-single // cpt)
    if single_tokens > max_input_tokens:
        single = max(1000, int(max_input_tokens * cpt))
        single_tokens = -(-single // cpt)
    ceiling = min(ceiling, int(context - single_tokens - overhead))
    ceiling = max(ceiling, output)
    chunk = max(constants["MIN_REASONING_CHUNK"], min(chunk, single))
    chunk = min(chunk, single)
    return profile_type(model, is_reasoning, context, max_output, output, single,
                        chunk, ceiling, list(features))


__all__ = ("build_model_profile",)
