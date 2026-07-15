"""Phase 5 contracts for extracted local maintenance commands."""

import argparse
import importlib
import unittest


class MaintenanceCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.maintenance_commands")

    def test_cache_clear_rejects_negative_age_before_removal(self):
        messages = []
        deps = self.core.CacheDependencies(
            cache_dir="/unused",
            usage_error=lambda message: messages.append(message),
            now=lambda: 100.0,
        )

        self.core.run_cache(
            argparse.Namespace(action="clear", older_than=-1), deps)

        self.assertEqual(len(messages), 1)
        self.assertIn("non-negative", messages[0])

    def test_empty_usage_payload_contains_no_money_fields(self):
        payload = self.core.empty_usage_payload(days=30, note="local only")

        self.assertEqual(payload["models"], [])
        self.assertTrue(payload["empty"])
        self.assertFalse({"cost", "price", "total_cost"} & set(payload))

    def test_uninstall_refuses_foreign_state_before_touching_key(self):
        touched = []
        deps = self.core.UninstallDependencies(
            state_dir="/foreign/state",
            foreign_root=lambda _path: "/foreign",
            keychain_delete=lambda: touched.append("delete"),
            keychain_read=lambda: None,
            save_config=lambda _values: touched.append("save"),
            command_link=lambda _args: touched.append("link"),
            launcher_name="ambient-codex",
            keychain_service="ambient-codex",
        )

        with self.assertRaises(SystemExit):
            self.core.run_uninstall(argparse.Namespace(purge=False, yes=True), deps)

        self.assertEqual(touched, [])


if __name__ == "__main__":
    unittest.main()
