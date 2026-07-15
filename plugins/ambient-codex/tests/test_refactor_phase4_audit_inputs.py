"""Phase 4 contracts for extracted audit rendering and repository intake."""

import importlib
import unittest


class AuditInputsTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.audit_inputs")
        deps = core.AuditInputDependencies.bind(repository=object())

        with self.assertRaises(TypeError):
            deps.bindings["repository"] = object()


if __name__ == "__main__":
    unittest.main()
