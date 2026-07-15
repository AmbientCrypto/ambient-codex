"""The server must exit when the client goes away, even mid-picker.

`MessageReader._pump` enqueues exactly one EOF sentinel and dies. Before EOF was
sticky, `elicit()` consumed that sentinel and `serve()`'s unbounded `get()` then
blocked forever on an empty queue behind a dead thread: the process wedged holding
its stdio pipes until SIGKILL whenever a client closed stdin (or sent one corrupt
frame) while a model picker was open.
"""
import importlib.util
import io
import json
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "mcp" / "ambient_mcp.py"


def load_mcp():
    spec = importlib.util.spec_from_file_location("ambient_mcp_lifecycle", MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode_jsonl(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


class TestEofIsSticky(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()

    def test_eof_is_reported_to_every_caller_not_just_the_first(self):
        reader = self.mcp.MessageReader(io.BytesIO())
        self.assertIsNone(reader.get(timeout=5))
        try:
            second = reader.get(timeout=5)
        except queue.Empty:  # pragma: no cover - the bug this test pins
            self.fail("EOF was consumed once; the next caller would block forever")
        self.assertIsNone(second)

    def test_at_eof_is_false_while_messages_remain(self):
        stream = io.BytesIO(encode_jsonl({"jsonrpc": "2.0", "id": 1, "method": "ping"}))
        reader = self.mcp.MessageReader(stream)
        first = reader.get(timeout=5)
        self.assertIsNotNone(first)
        self.assertIsNone(reader.get(timeout=5))
        self.assertTrue(reader.at_eof())

    def test_a_malformed_frame_ends_the_stream_for_every_caller(self):
        reader = self.mcp.MessageReader(io.BytesIO(b"{not json\n"))
        self.assertIsNone(reader.get(timeout=5))
        self.assertIsNone(reader.get(timeout=5))


class TestServeExitsOnClientHangup(unittest.TestCase):
    """Drive the real serve() in-process; it must return rather than wedge."""

    def setUp(self):
        self.mcp = load_mcp()

    def _serve_with(self, stdin_bytes, deadline=8.0):
        self.mcp.SESSION = self.mcp.Session()
        stdin = io.BytesIO(stdin_bytes)
        stdout = io.BytesIO()
        done = threading.Event()

        def run():
            try:
                self.mcp.serve()
            finally:
                done.set()

        real_stdin, real_stdout = sys.stdin, sys.stdout
        sys.stdin = type("S", (), {"buffer": stdin})()
        sys.stdout = type("S", (), {"buffer": stdout})()
        try:
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            finished = done.wait(deadline)
        finally:
            sys.stdin, sys.stdout = real_stdin, real_stdout
        return finished, stdout.getvalue()

    def test_clean_eof_with_no_picker_exits(self):
        finished, _ = self._serve_with(b"")
        self.assertTrue(finished, "serve() did not return on a clean EOF")

    def test_stdin_eof_while_a_picker_is_open_exits(self):
        """The reported wedge: client closes stdin without answering the picker."""
        stdin = (
            encode_jsonl({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18",
                                     "capabilities": {"elicitation": {}}}})
            + encode_jsonl({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "ambient_pick_model", "arguments": {}}})
        )
        serving = [{"id": "a/b", "ready": True, "hidden": False}]
        original = self.mcp._serving_models
        self.mcp._serving_models = lambda: serving
        try:
            finished, out = self._serve_with(stdin)
        finally:
            self.mcp._serving_models = original
        self.assertTrue(finished, "serve() wedged after stdin EOF during a picker")
        self.assertIn(b"elicitation/create", out)

    def test_a_corrupt_frame_during_a_picker_exits(self):
        stdin = (
            encode_jsonl({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18",
                                     "capabilities": {"elicitation": {}}}})
            + encode_jsonl({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "ambient_pick_model", "arguments": {}}})
            + b"{ this is not json\n"
        )
        serving = [{"id": "a/b", "ready": True, "hidden": False}]
        original = self.mcp._serving_models
        self.mcp._serving_models = lambda: serving
        try:
            finished, _ = self._serve_with(stdin)
        finally:
            self.mcp._serving_models = original
        self.assertTrue(finished, "serve() wedged after a corrupt frame during a picker")


class TestServeProcessExits(unittest.TestCase):
    def test_real_process_exits_when_stdin_closes_mid_picker(self):
        """End-to-end: no lingering process holding the stdio pipes."""
        stdin = (
            encode_jsonl({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18",
                                     "capabilities": {"elicitation": {}}}})
            + encode_jsonl({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "ambient_pick_model",
                                       "arguments": {"lane": "chat"}}})
        )
        proc = subprocess.run(
            [sys.executable, "-u", str(MCP)], input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
