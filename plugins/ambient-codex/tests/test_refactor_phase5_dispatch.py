"""Phase 5 contracts for immutable CLI registry and parse orchestration."""

import argparse
import importlib
import unittest


class DispatchCoreTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.cli_dispatch")

    def test_registry_is_complete_and_immutable(self):
        configured = {}

        def configure(name):
            def add(sub):
                configured[name] = sub.add_parser(name)
            return add

        callbacks = {
            name: configure(name) for name in self.core.COMMAND_NAMES
        }
        registry = self.core.make_command_registry(callbacks)

        self.assertIsInstance(registry, tuple)
        self.assertEqual(
            tuple(spec["name"] for spec in registry), self.core.COMMAND_NAMES)
        with self.assertRaises(TypeError):
            registry[0]["handler"] = "replacement"

    def test_parse_stdin_sentinel_returns_fresh_prompt_state(self):
        original_prompt = ["summarize"]

        class FakeParser:
            def parse_known_args(self, _argv):
                return argparse.Namespace(prompt=original_prompt), ["-"]

            def error(self, message):
                raise AssertionError(message)

        parsed = self.core.parse_args_with_stdin_dash(FakeParser(), ["ask"])

        self.assertEqual(original_prompt, ["summarize"])
        self.assertEqual(parsed.prompt, ["summarize", "-"])
        self.assertIsNot(parsed.prompt, original_prompt)

    def test_unknown_parse_extras_still_use_parser_error_contract(self):
        class FakeParser:
            def parse_known_args(self, _argv):
                return argparse.Namespace(), ["--bad"]

            def error(self, message):
                raise ValueError(message)

        with self.assertRaisesRegex(ValueError, "unrecognized arguments: --bad"):
            self.core.parse_args_with_stdin_dash(FakeParser(), [])


if __name__ == "__main__":
    unittest.main()
