"""Phase 5 contracts for focused code and interactive chat bindings."""

import importlib
import unittest


class GenerationCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.generation_commands")

    def test_dependencies_are_a_frozen_copy(self):
        source = {"route_model": object()}
        deps = self.core.GenerationDependencies.bind(**source)
        source["route_model"] = object()

        self.assertIsNot(deps.route_model, source["route_model"])
        with self.assertRaises(TypeError):
            deps.bindings["route_model"] = object()


if __name__ == "__main__":
    unittest.main()
