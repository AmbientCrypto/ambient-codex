"""Immutable session, request, and retry state for completion workflows."""

import dataclasses
import threading
import weakref

from .constants import DEFAULT_TIMEOUT_S, MAX_AUTO_BUDGET_TOKENS


DEFAULT_BUDGET_ESCALATIONS = 1
MAX_BUDGET_ESCALATIONS = 2
MAX_COMPLETE_ATTEMPTS = 5
_SESSION_CATALOG = weakref.WeakKeyDictionary()


@dataclasses.dataclass(frozen=True, eq=False)
class Session:
    """One immutable transport context with a write-once catalog sidecar."""

    api_url: str
    api_key: str
    conf: dict = dataclasses.field(default_factory=dict)
    transport: object = None
    _catalog_lock: object = dataclasses.field(
        default_factory=threading.Lock, repr=False, compare=False)

    def _load_catalog(self):
        raise NotImplementedError("Session subclasses must provide a catalog loader")

    def catalog(self):
        with self._catalog_lock:
            if self not in _SESSION_CATALOG:
                _SESSION_CATALOG[self] = self._load_catalog()
            return _SESSION_CATALOG[self]


@dataclasses.dataclass(frozen=True)
class RequestSpec:
    """Frozen carrier for every execution-engine request knob."""

    max_tokens: object = None
    temperature: float = 0.1
    timeout: int = DEFAULT_TIMEOUT_S
    response_format: object = None
    system: object = None
    raw: bool = False
    json: bool = False
    format: object = None
    allow_partial: bool = False
    allow_cost: bool = False
    yes: bool = False
    parallel: object = None
    no_cache: bool = False
    cache_ttl: object = None
    fallback: bool = False
    consensus: object = None
    _no_fallback: bool = False
    _auto_budget: bool = False
    _cache_salt: object = None
    escalation_ceiling: int = MAX_AUTO_BUDGET_TOKENS
    max_budget_escalations: int = DEFAULT_BUDGET_ESCALATIONS
    gate_fallback: bool = True

    @classmethod
    def from_args(cls, args):
        if isinstance(args, cls):
            return args
        return cls(**{
            field.name: getattr(args, field.name, field.default)
            for field in dataclasses.fields(cls)
        })

    def with_budget_policy(self, profile, input_chars, *, resolve_output_budget,
                           context_safe_escalation_ceiling, effective_cpt):
        """Return a budgeted copy using the caller's model-policy functions."""
        resolved, automatic = resolve_output_budget(
            self.max_tokens, profile, input_chars)
        ceiling = context_safe_escalation_ceiling(
            profile, input_chars, effective_cpt(profile.model))
        return dataclasses.replace(
            self, max_tokens=resolved, _auto_budget=automatic,
            escalation_ceiling=max(resolved, ceiling),
        )


@dataclasses.dataclass(frozen=True)
class AttemptState:
    """One immutable state in the bounded retry and fallback ladder."""

    model: str
    messages: object
    spec: RequestSpec
    stall_retried: bool = False
    budget_escalations: int = 0
    fallback_retried: bool = False
    budget_shrunk: bool = False
    attempt_no: int = 0


def reasoning_str(*values):
    """Return the first non-empty string from untrusted reasoning fields."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def budget_escalation_limit(value):
    """Clamp a requested retry policy to the small engine maximum."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_BUDGET_ESCALATIONS
    return max(
        DEFAULT_BUDGET_ESCALATIONS,
        min(MAX_BUDGET_ESCALATIONS, parsed),
    )


__all__ = (
    "AttemptState",
    "DEFAULT_BUDGET_ESCALATIONS",
    "MAX_BUDGET_ESCALATIONS",
    "MAX_COMPLETE_ATTEMPTS",
    "RequestSpec",
    "Session",
    "budget_escalation_limit",
    "reasoning_str",
)
