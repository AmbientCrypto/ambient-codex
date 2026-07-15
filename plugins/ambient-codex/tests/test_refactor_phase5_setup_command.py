"""Phase 5 contracts for secure setup command orchestration."""

import importlib
import os
import unittest
from unittest import mock


class SetupCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.setup_command")

    def test_bearer_prefix_is_removed_without_touching_key_body(self):
        self.assertEqual(
            self.core.normalize_pasted_key("  Bearer abc_DEF-123  "),
            "abc_DEF-123",
        )

    def test_environment_presence_check_never_reads_the_secret_value(self):
        class PresenceOnlyEnvironment:
            def __contains__(self, name):
                return name == "AMBIENT_CODEX_API_KEY"

            def get(self, _name):
                raise AssertionError("secret value must not be read")

        with mock.patch.object(os, "environ", PresenceOnlyEnvironment()):
            self.assertTrue(self.core.environment_variable_is_set(
                "AMBIENT_CODEX_API_KEY"))

    def test_environment_override_note_contains_no_dynamic_key_name(self):
        note = self.core.environment_override_note()

        self.assertIn("key override", note)
        self.assertNotIn("AMBIENT_CODEX_API_KEY", note)
        self.assertEqual(
            self.core.normalize_pasted_key("abc_DEF-123"),
            "abc_DEF-123",
        )


if __name__ == "__main__":
    unittest.main()
