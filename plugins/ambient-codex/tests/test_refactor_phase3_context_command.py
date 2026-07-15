"""Phase 3 contracts for extracted input and code-context composition."""

import importlib
import unittest


class ContextCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.context_command")
        deps = core.ContextDependencies.bind(intake=object())

        with self.assertRaises(TypeError):
            deps.bindings["intake"] = object()


if __name__ == "__main__":
    unittest.main()
