"""Phase 4 contracts for code-generation workflow policies."""

import importlib
from types import SimpleNamespace
import unittest


class CodeWorkflowTests(unittest.TestCase):
    def test_request_clone_is_immutable_from_the_callers_perspective(self):
        core = importlib.import_module("ambient_codex.code_workflow")
        original = SimpleNamespace(temperature=0.0, max_tokens=None)
        cloned = core.clone_request(original)
        cloned.temperature = 0.7
        cloned.max_tokens = 8192
        self.assertEqual(original.temperature, 0.0)
        self.assertIsNone(original.max_tokens)

    def test_final_messages_are_immutable_and_preserve_context_boundaries(self):
        core = importlib.import_module("ambient_codex.code_workflow")
        task = "Add validation"
        context = "def existing():\n    pass\n"
        messages = core.final_messages(task, context)
        self.assertEqual(messages[1]["content"],
                         "Add validation\n\nContext files:\n\ndef existing():\n    pass\n")
        self.assertIn("senior software engineer", messages[0]["content"])
        self.assertEqual(task, "Add validation")
        self.assertEqual(context, "def existing():\n    pass\n")

    def test_context_is_clamped_to_the_final_prompt_room_immutably(self):
        core = importlib.import_module("ambient_codex.code_workflow")
        context = "a" * 1001
        clamped = core.clamp_context(
            context, task="t" * 600, single_shot_chars=2_000)
        self.assertEqual(clamped[:1000], "a" * 1000)
        self.assertTrue(clamped.endswith("[ambient: context truncated to fit]"))
        self.assertEqual(context, "a" * 1001)

    def test_final_dispatch_scopes_best_of_temperature_to_generation(self):
        core = importlib.import_module("ambient_codex.code_workflow")
        args = SimpleNamespace(temperature=0.0)
        calls = []

        core.dispatch_generation(
            "key", "url", "model", "task", "context", args,
            best_of_k=3, best_of_temperature=0.7, catalog={}, conf={},
            session="session",
            best_of_chat=lambda *pos, **kw: calls.append((pos, kw)),
            chat=lambda *pos, **kw: self.fail("single chat must not run"),
        )

        self.assertEqual(args.temperature, 0.0)
        self.assertEqual(len(calls), 1)
        positional, keywords = calls[0]
        self.assertEqual(positional[4].temperature, 0.7)
        self.assertEqual(positional[5], 3)
        self.assertEqual(keywords["kind"], "code")
