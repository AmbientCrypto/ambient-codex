"""Pure candidate selection for explicitly delegated automatic routing."""


AUTO_MODEL_SPECS = ("auto", "auto:cheapest", "auto:largest")


def is_auto_model(model):
    """True only for the explicit, case-insensitive auto pseudo-models."""
    return isinstance(model, str) and model.strip().lower() in AUTO_MODEL_SPECS


def select_auto_model(spec, catalog, *, is_ready, is_hidden, context_length,
                      output_price, fits):
    """Return ``(model_id, reason)`` for an explicit ``auto`` selection.

    The caller supplies catalog normalization and user-curation policy. Empty
    results deliberately remain ``None`` so the CLI can issue its established
    model-specific diagnosis rather than silently substituting anything.
    """
    normalized = spec.strip().lower()
    largest = normalized == "auto:largest"
    candidates = [
        item for item in catalog or []
        if isinstance(item, dict)
        and isinstance(item.get("id"), str) and item["id"]
        and is_ready(item) and not is_hidden(item)
    ]
    if largest:
        ranked = sorted(
            candidates,
            key=lambda item: (-context_length(item), output_price(item), item["id"]),
        )
        if not ranked:
            return None
        return ranked[0]["id"], f"largest READY context, {context_length(ranked[0])}"
    ranked = sorted(
        (item for item in candidates if fits(item)),
        key=lambda item: (output_price(item), -context_length(item), item["id"]),
    )
    if not ranked:
        return None
    return ranked[0]["id"], "cheapest READY that fits"


__all__ = ("is_auto_model", "select_auto_model")
