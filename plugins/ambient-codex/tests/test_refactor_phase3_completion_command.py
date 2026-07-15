"""Phase 3 contracts for the extracted completion state machine."""

import importlib
import unittest


class CompletionCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.completion_command")

    def test_dependency_bindings_are_immutable(self):
        deps = self.core.CompletionDependencies.bind(stream_completion=object())

        with self.assertRaises(TypeError):
            deps.bindings["stream_completion"] = object()


if __name__ == "__main__":
    unittest.main()
