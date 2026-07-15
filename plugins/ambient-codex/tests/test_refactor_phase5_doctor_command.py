"""Phase 5 contracts for extracted health diagnostics."""

import importlib
import unittest
from types import SimpleNamespace
from unittest import mock


class DoctorCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.doctor_command")

    def test_check_line_keeps_stable_alignment(self):
        line = self.core.format_check_line(
            "runtime", True, "python3 -> /usr/bin/python3",
            paint=lambda text, _color: text,
        )

        self.assertEqual(
            line, "PASS  runtime        python3 -> /usr/bin/python3")

    def test_auth_status_uses_fixed_categories_not_provider_detail(self):
        self.assertEqual(
            self.core.auth_status_detail(True, "ok"),
            "authentication verified",
        )
        self.assertEqual(
            self.core.auth_status_detail(False, "key"),
            "API key rejected",
        )
        self.assertEqual(
            self.core.auth_status_detail(False, "unexpected"),
            "authentication check failed",
        )

    def test_unknown_key_backend_is_not_reflected_to_output(self):
        self.assertEqual(
            self.core.key_backend_label("secret-provider-value"),
            "configured",
        )

    def test_owned_stable_launcher_is_reported_as_active(self):
        launcher = "/home/example/.local/bin/ambient-codex"
        deps = SimpleNamespace(
            launcher_name="ambient-codex",
            which=lambda name: launcher if name == "ambient-codex" else None,
            bundled_cli_path=lambda: "/plugin/bin/ambient",
            stable_launcher_is_ours=lambda path: path == launcher,
        )
        report = mock.Mock()

        self.core._report_launchers(report, deps)

        report.assert_called_once_with(
            "launcher", True,
            "`ambient-codex` on PATH is the active Codex launcher",
        )


if __name__ == "__main__":
    unittest.main()
