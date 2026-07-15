"""Bounded text-file intake with no import-time external effects."""

import array
import math
import os
import queue
import stat


_STDIN_WAIT_MIN_SECONDS = 0.1


def _validate_char_cap(char_cap):
    if isinstance(char_cap, bool) or not isinstance(char_cap, int) or char_cap <= 0:
        raise ValueError("character ceiling must be a positive integer")


def _regular_file_error(path):
    try:
        file_stat = os.lstat(path)
    except OSError as err:
        return str(err)
    if not stat.S_ISREG(file_stat.st_mode):
        return "not a regular file"
    return None


def _regular_open(path):
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as err:
        return None, str(err)
    try:
        opened_stat = os.fstat(descriptor)
    except OSError as err:
        os.close(descriptor)
        return None, str(err)
    if not stat.S_ISREG(opened_stat.st_mode):
        os.close(descriptor)
        return None, "not a regular file"
    return descriptor, None


def _read_text(path, max_chars):
    descriptor, error = _regular_open(path)
    if error is not None:
        return None, error
    try:
        source = os.fdopen(
            descriptor,
            "r",
            encoding="utf-8",
            errors="replace",
            newline="",
        )
    except (OSError, ValueError) as err:
        os.close(descriptor)
        return None, str(err)
    try:
        with source:
            return source.read(max_chars + 1), None
    except OSError as err:
        return None, str(err)


def read_files(paths, char_cap):
    """Return immutable ``(chunks, warnings, overflow_path)`` batch results."""
    _validate_char_cap(char_cap)
    chunks = ()
    warnings = ()
    total = 0
    for path in paths:
        error = _regular_file_error(path)
        if error is not None:
            warnings = (*warnings, f"skipping {path} ({error})")
            continue
        text, error = _read_text(path, char_cap - total)
        if error is not None:
            warnings = (*warnings, f"skipping {path} ({error})")
            continue
        if "\x00" in text:
            warnings = (*warnings, f"skipping {path} (looks binary)")
            continue
        if len(text) > char_cap - total:
            return chunks, warnings, path
        if not text.strip():
            warnings = (*warnings, f"skipping {path} (empty)")
            continue
        chunks = (*chunks, (path, text))
        total += len(text)
    return chunks, warnings, None


def read_map_item(path, char_cap):
    """Return ``(text, error)`` for one bounded map file without printing."""
    _validate_char_cap(char_cap)
    if os.path.isdir(path):
        return None, (
            "is a directory — map takes one FILE per item; glob "
            f"its files instead (e.g. {path}/*.py)"
        )
    error = _regular_file_error(path)
    if error is not None:
        if error == "not a regular file":
            return None, error
        return None, f"unreadable ({error})"
    text, error = _read_text(path, char_cap)
    if error is not None:
        return None, f"unreadable ({error})"
    if "\x00" in text:
        return None, "looks binary — map sends text items only"
    if len(text) > char_cap:
        return None, (
            f"file exceeds the {char_cap:,}-char ceiling — too large for map; "
            "run ambient-codex audit on it instead"
        )
    if not text.strip():
        return None, "empty file"
    return text, None


def _validate_wait(wait_s, label="stdin wait"):
    if (
        isinstance(wait_s, bool)
        or not isinstance(wait_s, (int, float))
        or not math.isfinite(wait_s)
        or wait_s <= 0
    ):
        raise ValueError(f"{label} must be a finite positive number")


def stdin_wait_seconds(environment, default_wait, maximum_wait):
    """Return the finite bounded stdin wait from an explicit environment."""
    _validate_wait(default_wait, "default stdin wait")
    _validate_wait(maximum_wait, "maximum stdin wait")
    if default_wait > maximum_wait:
        raise ValueError("default stdin wait cannot exceed maximum stdin wait")
    raw_wait = environment.get("AMBIENT_STDIN_WAIT")
    if not raw_wait:
        return float(default_wait)
    try:
        candidate = float(raw_wait)
    except (TypeError, ValueError):
        return float(default_wait)
    if not math.isfinite(candidate):
        return float(default_wait)
    return min(max(_STDIN_WAIT_MIN_SECONDS, candidate), float(maximum_wait))


def _stdin_limit_error(char_cap):
    return (
        f"stdin exceeds {char_cap:,} chars (too large for one request) "
        "— split the job (e.g. per-directory) as a cost sanity check."
    )


def _read_stdin_payload(stream, char_cap):
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream.read(char_cap + 1), False, None
        raw = buffer.read(char_cap * 4 + 1)
    except UnicodeDecodeError:
        return None, False, "stdin is not valid UTF-8 text"
    except (OSError, ValueError) as err:
        return None, False, f"cannot read stdin: {err}"
    if not isinstance(raw, (bytes, bytearray)):
        return None, False, "stdin byte stream returned non-bytes data"
    binary = b"\x00" in raw
    if len(raw) > char_cap * 4:
        return None, binary, _stdin_limit_error(char_cap)
    cleaned = bytes(raw).replace(b"\x00", b"") if binary else bytes(raw)
    return cleaned.decode("utf-8", errors="replace"), binary, None


def read_stdin_text(stream, char_cap):
    """Return immutable decoded stdin text, warnings, and an explicit error."""
    _validate_char_cap(char_cap)
    data, binary, error = _read_stdin_payload(stream, char_cap)
    warnings = (
        ("stdin looks binary — decoding lossily (NUL bytes stripped)",)
        if binary
        else ()
    )
    if error is not None:
        return None, warnings, error
    if not isinstance(data, str):
        return None, warnings, "stdin text stream returned non-text data"
    if len(data) > char_cap:
        return None, warnings, _stdin_limit_error(char_cap)
    return data, warnings, None


def read_stdin_bounded(reader, wait_s, thread_factory):
    """Read on a daemon thread and preserve exceptions for the caller thread."""
    _validate_wait(wait_s)
    if not callable(reader) or not callable(thread_factory):
        raise ValueError("stdin reader and thread factory must be callable")
    outcomes = queue.Queue(maxsize=1)

    def worker():
        try:
            outcome = ("data", reader())
        except BaseException as err:
            outcome = ("error", err)
        outcomes.put(outcome)

    thread = thread_factory(target=worker, daemon=True)
    thread.start()
    thread.join(wait_s)
    if thread.is_alive():
        return None, True
    try:
        kind, value = outcomes.get_nowait()
    except queue.Empty as err:
        raise RuntimeError("stdin reader thread ended without a result") from err
    if kind == "error":
        raise value
    if not isinstance(value, str):
        raise TypeError("stdin reader must return text")
    return value, False


def stdin_ready(stream, selector, wait_s):
    """Return True/False for readiness, or None when select is unsupported."""
    _validate_wait(wait_s)
    try:
        ready, _, _ = selector([stream], [], [], wait_s)
    except (OSError, ValueError, TypeError):
        return None
    return bool(ready)


def stdin_has_waiting_data(stream, selector, fcntl_module):
    """Conservatively detect bytes waiting on stdin without blocking."""
    try:
        if stream.isatty():
            return False
        ready, _, _ = selector([stream], [], [], 0)
        if not ready or fcntl_module is None:
            return False
        import termios

        count = array.array("i", [0])
        fcntl_module.ioctl(stream.fileno(), termios.FIONREAD, count)
        return count[0] > 0
    except (OSError, ValueError, ImportError, AttributeError, TypeError):
        return False


__all__ = (
    "read_files",
    "read_map_item",
    "stdin_wait_seconds",
    "read_stdin_text",
    "read_stdin_bounded",
    "stdin_ready",
    "stdin_has_waiting_data",
)
