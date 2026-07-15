"""Phase 5 contracts for extracted mode and settings commands."""

import argparse
import importlib
import unittest


class SettingsCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.settings_commands")

    def test_boolean_normalization_is_strict(self):
        self.assertEqual(self.core.normalize_bool(" YES "), "on")
        self.assertEqual(self.core.normalize_bool("false"), "off")
        with self.assertRaisesRegex(ValueError, "expected on or off"):
            self.core.normalize_bool("sometimes")

    def test_mode_write_uses_injected_state_adapter(self):
        writes = []
        deps = self.core.ModeDependencies(
            save_config=writes.append,
            read_config=lambda: {},
            resolve_key=lambda _conf: (None, None),
            resolve_model=lambda _args, _conf, lane: lane,
            launcher_name="ambient-codex",
        )

        self.core.run_mode(argparse.Namespace(state="takeover"), deps)

        self.assertEqual(writes, [{"AMBIENT_DELEGATE": "takeover"}])

    def test_config_update_is_whitelist_bounded(self):
        writes = []
        setting = {
            "name": "streaming", "env": "AMBIENT_PROGRESS",
            "default": "on", "how": "on|off",
            "validate": self.core.normalize_bool,
            "current": lambda _conf: "on",
        }
        deps = self.core.ConfigDependencies(
            settings=(setting,),
            save_config=writes.append,
            read_config=lambda: {},
            resolve_key=lambda _conf: (None, None),
            resolve_model=lambda _args, _conf, lane: lane,
            usage_error=lambda message: (_ for _ in ()).throw(ValueError(message)),
            launcher_name="ambient-codex",
            secret_patterns=(),
            environ={},
        )

        self.core.run_config(
            argparse.Namespace(verb="set", name="streaming", value="off"),
            deps,
        )

        self.assertEqual(writes, [{"AMBIENT_PROGRESS": "off"}])

    def test_control_model_projection_is_token_and_role_only(self):
        item = self.core.control_model_item(
            {
                "id": "vendor/model", "name": "Model", "is_ready": True,
                "context_length": 100_000, "max_output_length": 20_000,
                "supported_features": ["json"],
            },
            chat_default="vendor/model",
            code_default="other/model",
            hidden_ids=frozenset(),
            notes={"vendor/model": "fast"},
            as_bool=bool,
        )

        self.assertTrue(item["is_chat_default"])
        self.assertFalse(item["is_code_default"])
        self.assertEqual(item["note"], "fast")
        self.assertFalse({"cost", "price", "saved"} & set(item))


if __name__ == "__main__":
    unittest.main()
