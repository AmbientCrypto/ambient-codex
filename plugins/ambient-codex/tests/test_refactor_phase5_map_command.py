"""Phase 5 contracts for extracted independent map orchestration."""

import importlib
import unittest


class MapCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.map_command")

    def test_dependency_bindings_are_immutable(self):
        deps = self.core.MapDependencies.bind(complete=object())

        with self.assertRaises(TypeError):
            deps.bindings["complete"] = object()


if __name__ == "__main__":
    unittest.main()
