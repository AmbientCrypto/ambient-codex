"""Pure, namespaced OpenCode provider-config updates."""


def update_provider_config(config, *, provider, api_url, model):
    """Return an immutable provider update, or ``None`` for an unsafe shape."""
    if not isinstance(config, dict):
        return None
    providers = config.get("provider", {})
    if not isinstance(providers, dict):
        return None
    options = {"baseURL": f"{api_url}/v1", "apiKey": "{env:AMBIENT_CODEX_API_KEY}"}
    existing = providers.get(provider)
    if isinstance(existing, dict):
        current_models = existing.get("models")
        models = current_models if isinstance(current_models, dict) else {}
        native_provider = {
            **existing,
            "options": options,
            "models": {**models, model: {"name": model}},
        }
    else:
        native_provider = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Ambient Codex (ambient.xyz)",
            "options": options,
            "models": {model: {"name": model}},
        }
    updated = {**config, "provider": {**providers, provider: native_provider}}
    return ({"$schema": "https://opencode.ai/config.json", **updated}
            if existing is None and "$schema" not in updated else updated)


def build_agent_argv(agent_args, *, provider, model):
    """Build a namespaced OpenCode command without credential material."""
    extra = list(agent_args)
    pure = ([] if any(arg in ("--pure", "--no-pure") for arg in extra)
            else ["--pure"])
    prefix = ["opencode", "--model", f"{provider}/{model}"]
    if extra and extra[0] == "run":
        return ["opencode", "run", *prefix[1:], *pure, *extra[1:]]
    return [*prefix, *pure, *extra]


__all__ = ("update_provider_config", "build_agent_argv")
