"""Dependency-injected Server-Sent Event completion transport.

The CLI facade supplies its existing policy constants and presentation hooks.
Keeping those collaborators explicit lets this transport stay hermetic while
preserving all established monkeypatch seams in the extensionless launcher.
"""

import http.client
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request


def stream_completion(api_url, api_key, payload, timeout, on_delta=None, *,
                      opener=None, network_error=RuntimeError,
                      stall_error=RuntimeError, stream_line_max=65536,
                      heartbeat_s=30, hard_wall_s=0, noprogress_s=120,
                      progress_enabled=None, stderr=None,
                      stderr_is_tty=None, clock=None):
    """Return one streamed chat completion as ``(status, body)``.

    The behavior is deliberately supplied by the facade: error classes,
    liveness limits, and progress presentation remain CLI policy rather than
    becoming hidden module globals.  HTTP errors return their parsed payload;
    connection and liveness failures raise the injected typed errors.
    """
    opener = opener or urllib.request.urlopen
    progress_enabled = progress_enabled or (lambda: False)
    stderr = stderr or sys.stderr
    stderr_is_tty = stderr_is_tty or (lambda: False)
    clock = clock or time.time
    url = f"{api_url}/v1/chat/completions"
    data = json.dumps(
        dict(payload, stream=True, stream_options={"include_usage": True})
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    start = clock()
    last_beat = start
    last_growth = start
    last_size = 0
    beat_open = False

    def close_beat():
        nonlocal beat_open
        if beat_open:
            stderr.write("\n")
            stderr.flush()
            beat_open = False

    content, reasoning, usage, finish = [], [], None, None
    try:
        response = opener(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": {"message": body[:2000]}}
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if retry_after and isinstance(parsed, dict):
            parsed["_retry_after"] = retry_after
        return error.code, parsed
    except urllib.error.URLError as error:
        raise network_error(f"cannot reach {url} ({error.reason})") from error
    except TimeoutError as error:
        raise network_error(f"{url} timed out connecting after {timeout}s") from error
    except (OSError, http.client.HTTPException) as error:
        raise network_error(f"cannot reach {url} ({error})") from error

    with response:
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/event-stream" not in content_type:
            try:
                raw_body = response.read()
            except (OSError, http.client.HTTPException) as error:
                raise network_error(
                    f"connection dropped reading response from {url} ({error})") from error
            try:
                return response.status, json.loads(
                    raw_body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                return response.status, {"error": {"message": "non-JSON response"}}

        terminated = False
        data_lines = []

        def handle_event():
            nonlocal usage, finish, terminated
            if not data_lines:
                return
            payload_str = "\n".join(data_lines)
            data_lines.clear()
            if payload_str.strip() == "[DONE]":
                terminated = True
                return
            try:
                event = json.loads(payload_str)
            except json.JSONDecodeError:
                return
            if not isinstance(event, dict):
                return
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices")
            for choice in choices if isinstance(choices, list) else []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                delta = delta if isinstance(delta, dict) else {}
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    content.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                for reasoning_key in ("reasoning_content", "reasoning"):
                    reasoning_piece = delta.get(reasoning_key)
                    if isinstance(reasoning_piece, str) and reasoning_piece:
                        reasoning.append(reasoning_piece)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                    terminated = True

        line_queue = queue.Queue(maxsize=256)

        def reader():
            try:
                while True:
                    line = response.readline(stream_line_max)
                    line_queue.put(line)
                    if not line:
                        break
            except BaseException as error:
                line_queue.put(error)

        threading.Thread(target=reader, daemon=True).start()
        while True:
            now = clock()
            if hard_wall_s and now - start > hard_wall_s:
                close_beat()
                raise stall_error(
                    f"generation exceeded the {hard_wall_s // 60}-minute hard wall",
                    "".join(content), "".join(reasoning), hard_wall=True)
            if now - last_growth > noprogress_s:
                close_beat()
                raise stall_error(
                    f"no new content for {noprogress_s}s despite an open stream "
                    f"({last_size:,} chars so far) — the stream stopped making progress",
                    "".join(content), "".join(reasoning))
            if progress_enabled() and now - last_beat >= heartbeat_s:
                count = sum(map(len, content)) or sum(map(len, reasoning))
                phase = "writing" if content else "thinking"
                message = f"ambient: …{phase} ({count:,} chars, {int(now - start)}s elapsed)"
                if stderr_is_tty() and threading.current_thread() is threading.main_thread():
                    stderr.write("\r\033[K" + message)
                    stderr.flush()
                    beat_open = True
                else:
                    print(message, file=stderr)
                last_beat = now
            try:
                raw = line_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if isinstance(raw, BaseException):
                close_beat()
                received = sum(map(len, content))
                if isinstance(raw, TimeoutError):
                    raise stall_error(
                        f"stream went silent for {timeout}s ({received} chars received before the stall)",
                        "".join(content), "".join(reasoning))
                raise stall_error(
                    f"connection dropped mid-stream ({raw}); {received} chars received",
                    "".join(content), "".join(reasoning))
            if not raw:
                handle_event()
                if not terminated:
                    close_beat()
                    raise stall_error(
                        "stream closed before completion "
                        f"({sum(map(len, content))} chars received, no end marker)",
                        "".join(content), "".join(reasoning))
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                handle_event()
                if terminated:
                    break
            elif line.startswith("data:"):
                value = line[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)
            size = sum(map(len, content)) + sum(map(len, reasoning))
            if size > last_size:
                last_size, last_growth = size, now
    close_beat()
    return 200, {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "usage": usage,
        "finish_reason": finish,
    }


__all__ = ("stream_completion",)
