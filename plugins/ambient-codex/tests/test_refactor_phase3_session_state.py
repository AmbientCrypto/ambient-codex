"""Contracts for extracted immutable completion-session state."""

import dataclasses
import importlib
import unittest


class SessionStateTests(unittest.TestCase):
    def test_request_and_attempt_state_are_frozen(self):
        core = importlib.import_module("ambient_codex.session_state")
        spec = core.RequestSpec(max_tokens=1024)
        attempt = core.AttemptState(model="m", messages=(), spec=spec)

        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.max_tokens = 2048
        with self.assertRaises(dataclasses.FrozenInstanceError):
            attempt.model = "other"

    def test_session_catalog_loader_is_memoized(self):
        core = importlib.import_module("ambient_codex.session_state")
        calls = []

        class Session(core.Session):
            def _load_catalog(self):
                calls.append(self.api_url)
                return [{"id": "m"}]

        session = Session(api_url="https://example.test", api_key="key")
        self.assertIs(session.catalog(), session.catalog())
        self.assertEqual(calls, ["https://example.test"])


if __name__ == "__main__":
    unittest.main()
