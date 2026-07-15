"""Phase 4 contracts for extracted resumable build-state adapters."""

import importlib
import unittest


class BuildStateCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.build_state_command")
        deps = core.BuildStateDependencies.bind(build_workflow=object())

        with self.assertRaises(TypeError):
            deps.bindings["build_workflow"] = object()


if __name__ == "__main__":
    unittest.main()
