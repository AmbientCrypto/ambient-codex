"""Phase 4 contracts for extracted audit execution planning."""

import importlib
import unittest


class AuditPlanningTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.audit_planning")
        deps = core.AuditPlanningDependencies.bind(model_profile=object())

        with self.assertRaises(TypeError):
            deps.bindings["model_profile"] = object()


if __name__ == "__main__":
    unittest.main()
