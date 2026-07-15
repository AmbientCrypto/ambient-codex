"""Phase 5 contracts for extracted result rendering composition."""

import importlib
import unittest


class OutputCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.output_command")
        deps = core.OutputDependencies.bind(redact=object())

        with self.assertRaises(TypeError):
            deps.bindings["redact"] = object()


if __name__ == "__main__":
    unittest.main()
