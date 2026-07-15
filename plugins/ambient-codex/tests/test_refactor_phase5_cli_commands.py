"""Contracts for extracted CLI command-composition adapters."""

import importlib
import unittest


class CliCommandAdapterTests(unittest.TestCase):
    def test_config_boolean_normalization_is_preserved(self):
        commands = importlib.import_module("ambient_codex.cli_commands")
        settings = importlib.import_module("ambient_codex.settings_commands")
        (normalize,) = commands.build(
            {"_settings_commands": settings},
            "normalize=_config_norm_bool",
        )

        self.assertEqual(normalize("yes"), "on")
        self.assertEqual(normalize("off"), "off")

    def test_unknown_adapter_is_rejected(self):
        commands = importlib.import_module("ambient_codex.cli_commands")
        with self.assertRaises(ValueError):
            commands.build({}, "missing")


if __name__ == "__main__":
    unittest.main()
