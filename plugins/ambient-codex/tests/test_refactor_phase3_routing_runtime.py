"""Phase 3 contracts for extracted routing and budget composition."""

import importlib
import unittest


class RoutingRuntimeTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.routing_runtime")
        deps = core.RoutingDependencies.bind(routing=object())

        with self.assertRaises(TypeError):
            deps.bindings["routing"] = object()


if __name__ == "__main__":
    unittest.main()
