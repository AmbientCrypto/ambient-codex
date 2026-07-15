"""Dependency-free record and error types for Ambient Codex."""

import collections


ModelProfile = collections.namedtuple(
    "ModelProfile",
    "model is_reasoning context_length max_output_length "
    "output_budget single_shot_chars chunk_chars escalation_ceiling features",
)


class NetworkError(Exception):
    """The API was unreachable (DNS, connection refused, timeout)."""


class ChatError(Exception):
    def __init__(self, category, diagnosis):
        super().__init__(diagnosis)
        self.category = category
        self.diagnosis = diagnosis


class StallError(Exception):
    """The stream went silent (or hit the hard wall) mid-generation."""

    def __init__(self, message, partial="", reasoning="", hard_wall=False):
        super().__init__(message)
        self.partial = partial
        self.reasoning = reasoning
        self.hard_wall = hard_wall


__all__ = ("ModelProfile", "NetworkError", "ChatError", "StallError")
