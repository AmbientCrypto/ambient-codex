"""Phase 2D1 contracts for bounded private cache state."""

import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = ("cache_key", "cache_get", "cache_put")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2d1", str(BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)


class CacheKeyTests(unittest.TestCase):
    def test_module_owns_exact_exports_and_stable_addresses(self):
        cache = importlib.import_module("ambient_codex.cache_store")

        self.assertEqual(cache.__all__, MOVED_NAMES)
        self.assertEqual(
            cache.cache_key("glm", "sys", "body", 8192, 0.1),
            "661f3b07c063373c220de0bd551843e54bff8e49e0d0e54e3fe850941208e299",
        )
        self.assertEqual(
            cache.cache_key(
                "glm", "sys", "body", 8192, 0.1,
                {"type": "json_schema"},
            ),
            "8e17d8872fd4185b63a7543f972e5bce9423cf2daf4ecd2cbdffa953057e46d8",
        )
        self.assertEqual(
            cache.cache_key("glm", "sys", "body", 8192, 0.1, salt="best-of:0"),
            "21b362e22c8e62260e8ea636bbd3520d6c8886f29e662f877635debfe2de8ab8",
        )

    def test_key_encoding_is_lossy_only_for_invalid_unicode(self):
        cache = importlib.import_module("ambient_codex.cache_store")

        first = cache.cache_key("m", "sys\ud800", "body", 1, 0)
        second = cache.cache_key("m", "sys?", "body", 1, 0)

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")


class CacheReadTests(unittest.TestCase):
    def test_missing_expired_and_valid_entries(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(cache.cache_get(td, "missing", 60))
            path = Path(td) / "entry.json"
            path.write_text(json.dumps({"text": "RESULT", "ts": 1}), encoding="utf-8")
            now = time.time()
            os.utime(path, (now - 120, now - 120))
            self.assertIsNone(cache.cache_get(td, "entry", 60, now=now))
            self.assertEqual(cache.cache_get(td, "entry", 0, now=now), "RESULT")

    def test_malformed_nonobject_and_nonstring_payloads_are_misses(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        payloads = (
            b"{", b"42", b"[]", b'{"text": 42}', b'{"other": "x"}',
            b"[" * 2_000 + b"0" + b"]" * 2_000,
        )
        with tempfile.TemporaryDirectory() as td:
            for index, payload in enumerate(payloads):
                with self.subTest(payload=payload):
                    (Path(td) / f"bad{index}.json").write_bytes(payload)
                    self.assertIsNone(cache.cache_get(td, f"bad{index}", 0))

    def test_unsafe_keys_and_nonregular_entries_fail_closed(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.json"
            target.write_text(json.dumps({"text": "SECRET"}), encoding="utf-8")
            link = Path(td) / "link.json"
            try:
                link.symlink_to(target)
            except OSError:
                link = None
            (Path(td) / "folder.json").mkdir()

            for key in ("../secret", "/absolute", "a/b", "a\\b", "", ".", ".."):
                with self.subTest(key=key):
                    self.assertIsNone(cache.cache_get(td, key, 0))
            self.assertIsNone(cache.cache_get(td, "folder", 0))
            if link is not None:
                self.assertIsNone(cache.cache_get(td, "link", 0))

    def test_oversized_entry_is_a_bounded_miss(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
                cache, "_CACHE_ENTRY_MAX_BYTES", 20,
        ):
            (Path(td) / "large.json").write_bytes(b'{"text":"' + b"x" * 30 + b'"}')
            self.assertIsNone(cache.cache_get(td, "large", 0))

    def test_descriptor_is_closed_when_post_open_validation_fails(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        regular = os.stat(__file__)
        with mock.patch.object(cache.os, "lstat", return_value=regular), \
                mock.patch.object(cache.os, "open", return_value=42), \
                mock.patch.object(cache.os, "fstat", side_effect=OSError("failed")), \
                mock.patch.object(cache.os, "close") as close:
            self.assertIsNone(cache._open_cache_descriptor("entry.json"))
        close.assert_called_once_with(42)

    def test_descriptor_is_closed_when_stream_wrapping_fails(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with mock.patch.object(cache, "_open_cache_descriptor", return_value=42), \
                mock.patch.object(cache.os, "fdopen", side_effect=OSError("failed")), \
                mock.patch.object(cache.os, "close") as close:
            self.assertIsNone(cache._read_cache_payload("entry.json"))
        close.assert_called_once_with(42)


class CacheWriteTests(unittest.TestCase):
    def test_atomic_entry_is_private_and_round_trips(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as parent:
            directory = str(Path(parent) / "cache")
            cache.cache_put(directory, "key", "VALUE", 4_000, private_dir)
            path = Path(directory) / "key.json"

            self.assertEqual(cache.cache_get(directory, "key", 60), "VALUE")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["text"], "VALUE")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            self.assertEqual(list(Path(directory).glob(".tmp-*")), [])

    def test_invalid_key_or_payload_never_writes_outside_cache(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td:
            for key, value in (("../escape", "x"), ("key", object())):
                with self.subTest(key=key):
                    cache.cache_put(td, key, value, 10, private_dir)
            self.assertEqual(list(Path(td).iterdir()), [])
            self.assertFalse((Path(td).parent / "escape.json").exists())

    def test_prunes_oldest_deterministically_and_ignores_stat_races(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = time.time()
            for index in range(11):
                path = root / f"old-{index:02d}.json"
                path.write_text('{"text":"x"}', encoding="utf-8")
                os.utime(path, (now + index, now + index))
            real_stat = cache.os.stat

            def racing_stat(path):
                if os.fspath(path).endswith("old-05.json"):
                    raise FileNotFoundError(path)
                return real_stat(path)

            with mock.patch.object(cache.os, "stat", side_effect=racing_stat):
                cache.cache_put(td, "new", "VALUE", 10, private_dir)

            self.assertEqual(cache.cache_get(td, "new", 0), "VALUE")
            self.assertLessEqual(len(list(root.glob("*.json"))), 10)
            self.assertFalse((root / "old-05.json").exists())

    def test_replace_failure_cleans_temp_and_remains_best_effort(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
                cache.os, "replace", side_effect=OSError("denied"),
        ):
            self.assertIsNone(cache.cache_put(td, "key", "VALUE", 10, private_dir))
            self.assertEqual(list(Path(td).glob(".tmp-*")), [])
            self.assertIsNone(cache.cache_get(td, "key", 0))

    def test_concurrent_same_key_is_never_torn(self):
        cache = importlib.import_module("ambient_codex.cache_store")
        value = "X" * 10_000
        results = []
        with tempfile.TemporaryDirectory() as td:
            def put():
                for _ in range(20):
                    cache.cache_put(td, "same", value, 4_000, private_dir)

            def get():
                for _ in range(50):
                    results.append(cache.cache_get(td, "same", 3_600))

            workers = [threading.Thread(target=put) for _ in range(4)]
            workers.append(threading.Thread(target=get))
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

        self.assertTrue(all(result is None or result == value for result in results))


class CacheImportAndFacadeTests(unittest.TestCase):
    def test_import_is_side_effect_free_in_fresh_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.cache_store"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])

    def test_facade_keeps_patchable_signatures_and_runtime_paths(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._cache_store,
                    "cache_key",
                    return_value="digest",
            ) as key:
                self.assertEqual(
                    facade._cache_key("m", "s", "c", 1, 0.1, {"x": 1}, "salt"),
                    "digest",
                )
            key.assert_called_once_with("m", "s", "c", 1, 0.1, {"x": 1}, "salt")

            facade.CACHE_DIR = "/patched/cache"
            with mock.patch.object(facade._cache_store, "cache_get", return_value="hit") as get:
                self.assertEqual(facade._cache_get("key", 123), "hit")
            get.assert_called_once_with("/patched/cache", "key", 123)

            with mock.patch.object(facade._cache_store, "cache_put") as put:
                self.assertIsNone(facade._cache_put("key", "text"))
            put.assert_called_once_with(
                "/patched/cache", "key", "text", facade.CACHE_MAX_FILES,
                facade._private_dir,
            )


if __name__ == "__main__":
    unittest.main()
