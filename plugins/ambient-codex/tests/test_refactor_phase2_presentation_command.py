"""Phase 2 contracts for extracted terminal and model configuration adapters."""

import importlib
import unittest


class PresentationCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.presentation_command")
        deps = core.PresentationDependencies.bind(config_store=object())

        with self.assertRaises(TypeError):
            deps.bindings["config_store"] = object()


if __name__ == "__main__":
    unittest.main()
