"""Phase 5 contracts for extracted build orchestration bindings."""

import importlib
import unittest


class BuildCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.build_command")

    def test_dependency_bindings_are_copied_and_immutable(self):
        source = {"usage_error": object()}
        deps = self.core.BuildDependencies.bind(**source)
        source["usage_error"] = object()

        self.assertIsNot(deps.usage_error, source["usage_error"])
        with self.assertRaises(TypeError):
            deps.bindings["usage_error"] = object()


if __name__ == "__main__":
    unittest.main()
