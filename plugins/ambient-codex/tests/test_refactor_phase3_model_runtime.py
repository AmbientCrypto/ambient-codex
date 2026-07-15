"""Phase 3 contracts for extracted catalog and capability runtime adapters."""

import importlib
import unittest


class ModelRuntimeTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.model_runtime")
        deps = core.ModelRuntimeDependencies.bind(transport=object())

        with self.assertRaises(TypeError):
            deps.bindings["transport"] = object()


if __name__ == "__main__":
    unittest.main()
