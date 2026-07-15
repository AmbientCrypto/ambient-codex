"""Phase 3B contracts for the isolated SSE transport engine."""

import importlib
import json
import unittest


class _Response:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, lines):
        self._lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def readline(self, unused_limit):
        return next(self._lines, b"")


class _NetworkError(Exception):
    pass


class _StallError(Exception):
    def __init__(self, message, partial="", reasoning="", hard_wall=False):
        super().__init__(message)
        self.partial = partial
        self.reasoning = reasoning
        self.hard_wall = hard_wall


class StreamingTests(unittest.TestCase):
    def test_module_owns_the_stream_transport_export(self):
        core = importlib.import_module("ambient_codex.streaming")
        self.assertEqual(core.__all__, ("stream_completion",))

    def test_injected_transport_dependencies_assemble_sse_content(self):
        core = importlib.import_module("ambient_codex.streaming")
        event = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
        response = _Response([
            f"data: {event}\n".encode(), b"\n", b"data: [DONE]\n", b"\n",
        ])
        status, body = core.stream_completion(
            "https://example.invalid", "key", {"model": "test"}, 30,
            opener=lambda request, timeout: response,
            network_error=_NetworkError,
            stall_error=_StallError,
            progress_enabled=lambda: False,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["content"], "hello")

    def test_disabled_hard_wall_allows_progressing_stream_to_finish(self):
        core = importlib.import_module("ambient_codex.streaming")
        event = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
        response = _Response([
            f"data: {event}\n".encode(), b"\n", b"data: [DONE]\n", b"\n",
        ])
        ticks = iter((0, 10_000, 20_000, 30_000, 40_000, 50_000))
        status, body = core.stream_completion(
            "https://example.invalid", "key", {"model": "test"}, 30,
            opener=lambda request, timeout: response,
            network_error=_NetworkError,
            stall_error=_StallError,
            progress_enabled=lambda: False,
            hard_wall_s=0,
            noprogress_s=100_000,
            clock=lambda: next(ticks),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["content"], "hello")
