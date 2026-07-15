"""Contracts for extracted CLI parser and process entrypoint orchestration."""

import importlib
import unittest
from types import SimpleNamespace


class CliEntrypointTests(unittest.TestCase):
    def test_keyless_audit_route_is_pure_and_late_bound(self):
        entrypoint = importlib.import_module("ambient_codex.cli_entrypoint")
        (route,) = entrypoint.build({}, "audit_keyless_route=_audit_keyless_route")

        self.assertEqual(route(SimpleNamespace(install_hook="pre-commit")),
                         "cmd_audit_hook")
        self.assertIsNone(route(SimpleNamespace(
            install_hook=None, uninstall_hook=None)))

    def test_unknown_entrypoint_name_is_rejected(self):
        entrypoint = importlib.import_module("ambient_codex.cli_entrypoint")
        with self.assertRaises(ValueError):
            entrypoint.build({}, "missing")


if __name__ == "__main__":
    unittest.main()
