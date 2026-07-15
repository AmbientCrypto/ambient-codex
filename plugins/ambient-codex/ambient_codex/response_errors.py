"""Defensive normalization of untrusted Ambient API error responses."""

import json


_FUNDS_WORDS = (
    "credit", "account balance", "low balance", "insufficient funds",
    "insufficient balance", "out of funds", "quota", "billing", "payment",
)


def error_message(body):
    """Return a bounded string error message for any JSON response shape."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        return message if isinstance(message, str) else json.dumps(error)[:500]
    return json.dumps(body)[:500]


def classify_error(status, body, api_key, redact, launcher_name):
    """Map an API failure to a stable category and user-facing diagnosis."""
    message = redact(error_message(body), api_key)
    lower = message.lower()
    if status >= 500:
        return "service", (
            f"Ambient service problem (HTTP {status}: {message}). Nothing is wrong with "
            "your key or account — retry shortly."
        )
    if status == 402 or any(word in lower for word in _FUNDS_WORDS):
        return "funds", (
            f"Your Ambient account looks out of funds or over quota (HTTP {status}: "
            f"{message}). Top up your account — the service itself is fine."
        )
    if status in (401, 403) or "authentication_error" in lower:
        return "key", (
            f"Ambient rejected your API key (HTTP {status}). The key may be revoked, "
            "expired, or mistyped — this is NOT an Ambient outage. "
            f"Fix: {launcher_name} setup --force"
        )
    if status == 429 and "no workers" in lower:
        return "model", (
            "That model isn't serving right now — Ambient spins models up and "
            "down with demand. Your key and account are fine — pick a model "
            "that's serving (ambient-codex models) or retry shortly."
        )
    if status == 429:
        return "rate", f"The network asked us to slow down ({message}) — wait a moment and retry."
    if status == 400:
        if any(word in lower for word in (
                "context", "context_length", "maximum context", "too long",
                "reduce the length")):
            return "context", f"input exceeds the model's context window (HTTP {status}: {message})."
        if any(word in lower for word in ("maximum tokens", "max_tokens", "token limit")):
            return "budget", (
                f"the requested output budget exceeds this model's limit "
                f"(HTTP {status}: {message})."
            )
        if any(word in lower for word in (
                "unknown model", "not a valid model", "model not found")):
            return "model", (
                f"'{message}'. That model id isn't in the catalog — check the "
                "spelling or run `ambient-codex models` to see what's available."
            )
    return "unknown", f"HTTP {status}: {message}"


__all__ = ("error_message", "classify_error")
