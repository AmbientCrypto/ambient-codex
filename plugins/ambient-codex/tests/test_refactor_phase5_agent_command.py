"""Phase 5 contracts for the external OpenCode agent integration."""

import importlib
import json
import os
import tempfile
import unittest


class AgentCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.agent_command")

    def test_provider_config_contains_env_reference_not_literal_key(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "opencode.json")
            self.core.ensure_provider_config(
                path, "ambient-codex", "https://api.example", "model/id",
                lambda config, **_kwargs: {
                    **config,
                    "provider": {"apiKey": "{env:AMBIENT_CODEX_API_KEY}"},
                },
            )

            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(
                payload["provider"]["apiKey"],
                "{env:AMBIENT_CODEX_API_KEY}",
            )
            self.assertNotIn("sk-secret", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
