"""Phase 2 contracts for extracted runtime state and backend configuration."""

import importlib
import io
import sys
import unittest
from unittest import mock


class RuntimeCommandTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.runtime_command")
        deps = core.RuntimeDependencies.bind(state=object())

        with self.assertRaises(TypeError):
            deps.bindings["state"] = object()

    def test_unconfigured_exit_is_constant_only_and_never_accepts_a_key(self):
        core = importlib.import_module("ambient_codex.runtime_command")
        stderr = io.StringIO()

        with mock.patch.object(sys, "stderr", stderr), \
                self.assertRaises(SystemExit) as raised:
            core.exit_unconfigured(
                "ambient-codex", 3, sys,
            )

        self.assertEqual(raised.exception.code, 3)
        self.assertIn("no API key configured", stderr.getvalue())
        self.assertIn("https://ambient.xyz", stderr.getvalue())
        self.assertNotIn("secret-value", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
