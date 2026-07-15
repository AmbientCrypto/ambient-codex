"""Phase 5 contracts for extracted audit-hook management."""

import importlib
import unittest


class AuditHookCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.audit_hook_command")
        deps = core.AuditHookDependencies.bind(subprocess=object())

        with self.assertRaises(TypeError):
            deps.bindings["subprocess"] = object()


if __name__ == "__main__":
    unittest.main()
