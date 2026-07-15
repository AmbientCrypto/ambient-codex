"""Contracts for the extracted native MCP framing runtime."""

import importlib
import io
import unittest


class McpFramingTests(unittest.TestCase):
    def test_jsonl_and_content_length_frames_round_trip(self):
        framing = importlib.import_module("mcp.ambient_mcp_framing")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for mode in ("jsonl", "content-length"):
            stream = io.BytesIO()
            framing.write_message(stream, payload, framing=mode)
            stream.seek(0)
            self.assertEqual(framing.read_message(stream)["payload"], payload)

    def test_oversized_frame_is_rejected_before_reading_body(self):
        framing = importlib.import_module("mcp.ambient_mcp_framing")
        stream = io.BytesIO(b"Content-Length: 99\r\n\r\n{}")
        with self.assertRaisesRegex(ValueError, "exceeds 8 bytes"):
            framing.read_message(stream, max_frame_bytes=8)


if __name__ == "__main__":
    unittest.main()
