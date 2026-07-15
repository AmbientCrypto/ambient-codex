"""Phase 3C-3 contracts for model profile construction."""

import importlib
import unittest

from ambient_codex.records import ModelProfile


class ModelProfileCoreTests(unittest.TestCase):
    def test_module_owns_profile_builder(self):
        core = importlib.import_module("ambient_codex.model_profiles")
        self.assertEqual(core.__all__, ("build_model_profile",))

    def test_unknown_model_uses_conservative_reasoning_fallback(self):
        core = importlib.import_module("ambient_codex.model_profiles")
        constants = {
            "FALLBACK_CONTEXT": 200_000, "FALLBACK_MAX_OUTPUT": 16_384,
            "MAX_AUTO_BUDGET_TOKENS": 65_536, "MIN_OUTPUT_TOKENS": 2_048,
            "NONREASONING_OUTPUT_BUDGET": 16_384,
            "NONREASONING_CONTEXT_MARGIN": 0.85,
            "REASONING_CHUNK_FACTOR": 0.85,
            "MIN_REASONING_CHUNK": 8_000,
            "CONTEXT_OVERHEAD_TOKENS": 2_500,
            "OUTPUT_SAFETY": 1.15, "ANSWER_TOKENS_RESERVE": 6_000,
            "REASONING_EXPANSION": 1.5, "CHARS_PER_TOKEN": 3.2,
            "INPUT_TOKEN_SAFETY": 1.75,
        }
        profile = core.build_model_profile(
            [], "offline/model", lambda _model: 3.2,
            lambda value, default: int(value) if isinstance(value, int) and value > 0 else default,
            ModelProfile, constants, 120_000,
        )
        self.assertTrue(profile.is_reasoning)
        self.assertEqual(profile.context_length, 200_000)
        self.assertLessEqual(profile.output_budget, profile.max_output_length)
        self.assertLessEqual(profile.chunk_chars, profile.single_shot_chars)
