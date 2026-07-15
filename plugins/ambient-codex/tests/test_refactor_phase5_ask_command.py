"""Phase 5 contracts for extracted direct-ask orchestration."""

import importlib
import unittest


class AskCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.ask_command")

    def test_dependency_bindings_are_immutable(self):
        deps = self.core.AskDependencies.bind(complete=object())

        with self.assertRaises(TypeError):
            deps.bindings["complete"] = object()


if __name__ == "__main__":
    unittest.main()
