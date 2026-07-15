"""Phase 3B contracts for normalized API error messages and categories."""

import importlib
import unittest


class ResponseErrorTests(unittest.TestCase):
    def test_module_owns_error_normalization_exports(self):
        core = importlib.import_module("ambient_codex.response_errors")
        self.assertEqual(core.__all__, ("error_message", "classify_error"))

    def test_error_message_never_returns_non_string(self):
        core = importlib.import_module("ambient_codex.response_errors")
        self.assertEqual(core.error_message({"error": {"message": ["bad"]}}),
                         '{"message": ["bad"]}')
        self.assertEqual(core.error_message({"error": {"message": "bad"}}), "bad")
