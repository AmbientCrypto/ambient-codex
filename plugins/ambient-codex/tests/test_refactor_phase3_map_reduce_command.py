"""Phase 3 contracts for extracted map-reduce composition."""

import importlib
import unittest


class MapReduceCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.map_reduce_command")
        deps = core.MapReduceDependencies.bind(complete=object())

        with self.assertRaises(TypeError):
            deps.bindings["complete"] = object()


if __name__ == "__main__":
    unittest.main()
