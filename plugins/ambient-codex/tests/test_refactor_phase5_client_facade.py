"""Contracts for extracted client/completion facade adapters."""

import importlib
import unittest


class ClientFacadeTests(unittest.TestCase):
    def test_error_message_adapter_delegates_to_response_parser(self):
        client = importlib.import_module("ambient_codex.client_facade")
        errors = importlib.import_module("ambient_codex.response_errors")
        (message,) = client.build(
            {"_response_errors": errors}, "message=error_message")

        self.assertEqual(message({"error": {"message": "busy"}}), "busy")

    def test_unknown_adapter_is_rejected(self):
        client = importlib.import_module("ambient_codex.client_facade")
        with self.assertRaises(ValueError):
            client.build({}, "missing")


if __name__ == "__main__":
    unittest.main()
