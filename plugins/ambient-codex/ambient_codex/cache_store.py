"""Bounded private cache state for resumable Ambient model calls."""

import hashlib
import json
import os
import stat
import tempfile
import time


_CACHE_ENTRY_MAX_BYTES = 64_000_000


def cache_key(
        model, system, chunk, max_tokens, temperature,
        response_format=None, salt=None,
):
    """Return the stable content address for one model call."""
    digest = hashlib.sha256()
    digest.update(f"{model}\0{max_tokens}\0{temperature}\0".encode())
    digest.update(json.dumps(response_format, sort_keys=True).encode())
    digest.update(b"\0")
    digest.update(system.encode("utf-8", "replace"))
    digest.update(b"\0")
    digest.update(chunk.encode("utf-8", "replace"))
    if salt is not None:
        digest.update(f"\0salt:{salt}".encode())
    return digest.hexdigest()


def _cache_path(cache_dir, key):
    if (
        not isinstance(key, str)
        or not key
        or len(key) > 256
        or key in (".", "..")
        or "\0" in key
        or "/" in key
        or "\\" in key
        or os.path.basename(key) != key
    ):
        return None
    try:
        return os.path.join(os.fsdecode(os.fspath(cache_dir)), key + ".json")
    except (TypeError, UnicodeError, ValueError):
        return None


def _open_cache_descriptor(path):
    try:
        before = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > _CACHE_ENTRY_MAX_BYTES:
        return None
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_size > _CACHE_ENTRY_MAX_BYTES
        or not os.path.samestat(before, opened)
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    return descriptor


def _read_cache_payload(path):
    descriptor = _open_cache_descriptor(path)
    if descriptor is None:
        return None
    try:
        source = os.fdopen(descriptor, "rb")
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    try:
        with source:
            data = source.read(_CACHE_ENTRY_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _CACHE_ENTRY_MAX_BYTES:
        return None
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        return None
    return payload["text"]


def cache_get(cache_dir, key, ttl, *, now=None):
    """Return one current string cache value; every unsafe state is a miss."""
    path = _cache_path(cache_dir, key)
    if path is None:
        return None
    try:
        snapshot = os.lstat(path)
        current_time = time.time() if now is None else now
        if ttl and current_time - snapshot.st_mtime > ttl:
            return None
    except (OSError, TypeError, ValueError):
        return None
    return _read_cache_payload(path)


def _entry_mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _prune_cache(cache_dir, entries, max_files):
    if len(entries) <= max_files:
        return
    paths = tuple(os.path.join(cache_dir, entry) for entry in entries)
    ordered = tuple(sorted(paths, key=lambda path: (_entry_mtime(path), path)))
    for path in ordered[:len(paths) // 10 + 1]:
        try:
            os.unlink(path)
        except OSError:
            pass


def _write_atomic(path, cache_dir, text):
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(dir=cache_dir, prefix=".tmp-")
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            descriptor = None
            json.dump({"text": text, "ts": int(time.time())}, target)
        os.replace(temporary, path)
        temporary = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def cache_put(cache_dir, key, text, max_files, ensure_private_dir):
    """Best-effort atomic private cache write with approximate oldest pruning."""
    path = _cache_path(cache_dir, key)
    if (
        path is None
        or not isinstance(text, str)
        or isinstance(max_files, bool)
        or not isinstance(max_files, int)
        or max_files <= 0
    ):
        return None
    try:
        ensure_private_dir(cache_dir)
        entries = tuple(os.listdir(cache_dir))
        _prune_cache(cache_dir, entries, max_files)
        _write_atomic(path, cache_dir, text)
    except OSError:
        pass
    return None


__all__ = ("cache_key", "cache_get", "cache_put")
