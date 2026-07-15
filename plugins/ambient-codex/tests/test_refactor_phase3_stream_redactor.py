"""Contracts for the extracted streaming-safe redactor."""

import importlib
import re
import unittest


class StreamRedactorTests(unittest.TestCase):
    def test_split_secret_and_terminal_escape_never_leak(self):
        core = importlib.import_module("ambient_codex.stream_redactor")
        key = "ambient_test_secret_abcdefghijklmnopqrstuvwxyz"
        def redact(text, secret):
            sanitized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
            return (sanitized.replace(secret, "[REDACTED]")
                    if secret else sanitized)
        stream = core.StreamRedactor(key, redact)

        pieces = (
            stream.feed("before " + key[:9] + "\x1b["),
            stream.feed("31m" + key[9:] + " after"),
            stream.flush(),
        )

        output = "".join(pieces)
        self.assertEqual(output, "before [REDACTED] after")
        self.assertNotIn(key, output)
        self.assertNotIn("\x1b", output)

    def test_plain_chunks_avoid_repeated_full_sanitizer_scans(self):
        core = importlib.import_module("ambient_codex.stream_redactor")
        key = "ambient_test_secret_abcdefghijklmnopqrstuvwxyz"
        calls = []

        def redact(text, secret):
            calls.append((text, secret))
            sanitized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
            return (sanitized.replace(secret, "[REDACTED]")
                    if secret else sanitized)

        stream = core.StreamRedactor(key, redact)
        output = "".join(stream.feed("token ") for _ in range(1000))
        output += stream.flush()

        self.assertEqual(output, "token " * 1000)
        self.assertLessEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
