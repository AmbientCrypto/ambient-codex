"""Phase 3C-2 contracts for pure model budget primitives."""

import importlib
import unittest

from ambient_codex.records import ModelProfile


class ModelBudgetTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.model_budget")
        self.profile = ModelProfile("m", True, 100_000, 20_000, 8_000,
                                    30_000, 20_000, 16_000, ["reasoning"])
        self.constants = {
            "CHARS_PER_TOKEN": 4.0, "REASONING_EXPANSION": 1.5,
            "ANSWER_TOKENS_RESERVE": 6_000, "OUTPUT_SAFETY": 1.15,
            "INPUT_TOKEN_SAFETY": 1.75, "CONTEXT_OVERHEAD_TOKENS": 2_500,
            "MIN_REASONING_CHUNK": 8_000,
        }

    def test_module_owns_budget_and_response_format_exports(self):
        self.assertEqual(self.core.__all__, (
            "response_format_for", "reasoning_output_budget",
            "context_safe_output_cap", "context_safe_escalation_ceiling",
            "reasoning_single_shot_target", "resolve_output_budget",
        ))

    def test_response_format_respects_advertised_capabilities(self):
        schema = {"type": "object"}
        structured = self.profile._replace(features=["structured_outputs"])
        json_mode = self.profile._replace(features=["json_mode"])
        self.assertEqual(self.core.response_format_for(structured, schema)["type"],
                         "json_schema")
        self.assertEqual(self.core.response_format_for(json_mode, schema),
                         {"type": "json_object"})
        self.assertIsNone(self.core.response_format_for(self.profile, schema))

    def test_context_cap_and_escalation_never_overflow(self):
        cap = self.core.context_safe_output_cap(self.profile, 20_000, 4.0,
                                                self.constants)
        self.assertEqual(cap, 20_000)
        self.assertEqual(
            self.core.context_safe_escalation_ceiling(
                self.profile, 20_000, 4.0, self.constants), 16_000)

    def test_explicit_budget_is_clamped_to_context_without_mutating_profile(self):
        messages = []
        budget, automatic = self.core.resolve_output_budget(
            99_999, self.profile, 80_000, lambda _model: 4.0,
            self.constants, messages.append, 2_048,
        )
        self.assertFalse(automatic)
        self.assertLessEqual(budget, self.profile.max_output_length)
        self.assertEqual(messages, [])
