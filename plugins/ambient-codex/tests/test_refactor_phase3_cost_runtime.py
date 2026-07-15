"""Phase 3 contracts for extracted fallback-aware estimate composition."""

import importlib
import unittest


class CostRuntimeTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.cost_runtime")
        deps = core.CostDependencies.bind(model_pricing=object())

        with self.assertRaises(TypeError):
            deps.bindings["model_pricing"] = object()


if __name__ == "__main__":
    unittest.main()
