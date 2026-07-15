"""JSON-RPC framing and deadline-capable stdin reader for Ambient MCP."""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Dict, List, Optional, Union


DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024
JsonRpcPayload = Union[Dict[str, Any], List[Any]]
FramedPayload = Dict[str, Any]


def response_result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def response_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def requested_protocol_version(request: Dict[str, Any], default: str) -> str:
    params = request.get("params")
    if isinstance(params, dict):
        version = params.get("protocolVersion")
        if isinstance(version, str) and version.strip():
            return version
    return default


def is_notification(request: Dict[str, Any]) -> bool:
    return "id" not in request


def read_headers(stream, first_line: bytes) -> Dict[str, str]:
    headers = parse_header_line(first_line, {})
    while True:
        line = stream.readline()
        if line == b"" or not line.strip():
            return headers
        headers = parse_header_line(line, headers)


def parse_header_line(line: bytes, headers: Dict[str, str]) -> Dict[str, str]:
    key, separator, value = line.strip().decode("ascii").partition(":")
    if not separator:
        raise ValueError(f"invalid MCP header line: {key}")
    return {**headers, key.lower(): value.strip()}


def parse_payload_bytes(body: bytes) -> JsonRpcPayload:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError("JSON-RPC message must be an object or batch array")
    return payload


def read_message(stream, *, max_frame_bytes=DEFAULT_MAX_FRAME_BYTES
                 ) -> Optional[FramedPayload]:
    while True:
        first_line = stream.readline()
        if first_line == b"":
            return None
        stripped = first_line.strip()
        if stripped:
            break
    if stripped.startswith((b"{", b"[")):
        return {"payload": parse_payload_bytes(stripped), "framing": "jsonl"}
    headers = read_headers(stream, first_line)
    raw_length = headers.get("content-length")
    if raw_length is None:
        raise ValueError("missing Content-Length header")
    if not raw_length.isdigit():
        raise ValueError("Content-Length must be a decimal integer")
    length = int(raw_length)
    if length <= 0:
        raise ValueError("Content-Length must be greater than zero")
    if length > max_frame_bytes:
        raise ValueError(f"Content-Length exceeds {max_frame_bytes} bytes")
    body = stream.read(length)
    if len(body) != length:
        raise ValueError("incomplete MCP message body")
    return {"payload": parse_payload_bytes(body), "framing": "content-length"}


def write_message(stream, payload: JsonRpcPayload, *, framing: str) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if framing == "jsonl":
        stream.write(body + b"\n")
        stream.flush()
        return
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header + body)
    stream.flush()


class MessageReader:
    """Read framed stdin on a thread so callers can wait with deadlines."""

    _EOF = object()

    def __init__(self, stream, *, message_reader=read_message) -> None:
        self._message_reader = message_reader
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._eof = threading.Event()
        self._thread = threading.Thread(
            target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        while True:
            try:
                framed = self._message_reader(stream)
            except Exception:  # noqa: BLE001 - a bad frame ends only this stream
                self._eof.set()
                self._queue.put(self._EOF)
                return
            if framed is None:
                self._eof.set()
                self._queue.put(self._EOF)
                return
            self._queue.put(framed)

    def at_eof(self) -> bool:
        return self._eof.is_set() and self._queue.empty()

    def get(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if self.at_eof():
            return None
        item = self._queue.get(timeout=timeout)
        return None if item is self._EOF else item


__all__ = (
    "FramedPayload",
    "JsonRpcPayload",
    "MessageReader",
    "is_notification",
    "parse_header_line",
    "parse_payload_bytes",
    "read_headers",
    "read_message",
    "requested_protocol_version",
    "response_error",
    "response_result",
    "write_message",
)
