"""Phase 3 contracts for extracted telemetry and capability persistence."""

import importlib
import unittest


class CapabilityRuntimeTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.capability_runtime")
        deps = core.CapabilityDependencies.bind(telemetry=object())

        with self.assertRaises(TypeError):
            deps.bindings["telemetry"] = object()


if __name__ == "__main__":
    unittest.main()
