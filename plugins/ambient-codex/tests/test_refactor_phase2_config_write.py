"""Phase 2B3 contracts for locked atomic config persistence."""

import contextlib
import concurrent.futures
import importlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = (
    "parse_config_lines",
    "read_config_file",
    "claim_state_dir",
    "private_dir",
    "config_lock",
    "save_config_values",
)


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2b3", str(BIN))
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


def abort(message):
    raise SystemExit(message)


@contextlib.contextmanager
def passthrough_lock(_conf_dir):
    yield


class InternalConfigWriteTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        config_store = importlib.import_module("ambient_codex.config_store")

        self.assertEqual(config_store.__all__, MOVED_NAMES)

    def test_state_claim_is_scoped_private_and_non_destructive(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            other = root / "other"
            other.mkdir()
            config_store.claim_state_dir(
                str(other), str(root), ".ambient-codex", "1.9.0"
            )
            self.assertFalse((other / ".ambient-codex").exists())

            config_store.claim_state_dir(
                str(root), str(root), ".ambient-codex", "1.9.0"
            )
            marker = root / ".ambient-codex"
            self.assertEqual(marker.read_text(encoding="utf-8"), "ambient-codex 1.9.0\n")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(marker).st_mode), 0o600)

            marker.write_text("existing\n", encoding="utf-8")
            config_store.claim_state_dir(
                str(root), str(root), ".ambient-codex", "2.0.0"
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "existing\n")

            marker.unlink()
            with mock.patch("builtins.open", side_effect=OSError("marker denied")):
                config_store.claim_state_dir(
                    str(root), str(root), ".ambient-codex", "1.9.0"
                )
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_private_dir_creates_and_heals_owner_only(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "private"
            config_store.private_dir(str(path))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
            os.chmod(path, 0o755)
            config_store.private_dir(str(path))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)

            with mock.patch.object(
                config_store.os, "makedirs", side_effect=OSError("mkdir denied")
            ):
                config_store.private_dir(str(Path(td) / "blocked"))

    def test_posix_lock_claims_excludes_and_releases(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            flock = mock.Mock()
            fake_fcntl = types.SimpleNamespace(
                LOCK_EX=1, LOCK_UN=2, flock=flock
            )

            claim = mock.Mock()
            with config_store.config_lock(
                td, claim, fake_fcntl, abort, time.time, time.sleep
            ):
                self.assertEqual(claim.call_count, 1)
                self.assertEqual(flock.call_args.args[1], fake_fcntl.LOCK_EX)

            self.assertEqual(flock.call_args.args[1], fake_fcntl.LOCK_UN)

    def test_posix_lock_acquisition_error_fails_closed(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            def fail_lock(_descriptor, operation):
                if operation == 1:
                    raise OSError("lock denied")

            fake_fcntl = types.SimpleNamespace(
                LOCK_EX=1, LOCK_UN=2, flock=fail_lock
            )
            with self.assertRaises(SystemExit) as raised:
                with config_store.config_lock(
                    td, mock.Mock(), fake_fcntl, abort, time.time, time.sleep
                ):
                    self.fail("entered without an acquired lock")
            self.assertIn("cannot acquire config lock", str(raised.exception))

            with mock.patch.object(
                config_store.os, "open", side_effect=PermissionError("open denied")
            ):
                with self.assertRaises(RuntimeError) as raised:
                    with config_store.config_lock(
                        td, mock.Mock(), fake_fcntl, lambda _message: None,
                        time.time, time.sleep,
                    ):
                        self.fail("entered after a broken abort callback")
            self.assertIn("cannot open config lock", str(raised.exception))

    def test_fallback_lock_cleans_up_and_breaks_only_stale_files(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".env.lock"
            lock_path.write_text("dead", encoding="utf-8")
            old = time.time() - 60
            os.utime(lock_path, (old, old))

            with config_store.config_lock(
                td, mock.Mock(), None, abort, time.time, lambda _: None
            ):
                self.assertTrue(lock_path.exists())
                self.assertEqual(lock_path.read_text(encoding="utf-8"), str(os.getpid()))

            self.assertFalse(lock_path.exists())

    def test_fallback_lock_timeout_and_open_error_fail_closed(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".env.lock"
            lock_path.write_text("live", encoding="utf-8")
            fixed_now = os.path.getmtime(lock_path)
            entered = False
            with self.assertRaises(SystemExit) as raised:
                with config_store.config_lock(
                    td, mock.Mock(), None, abort,
                    lambda: fixed_now, lambda _: None,
                ):
                    entered = True
            self.assertFalse(entered)
            self.assertIn("config is locked", str(raised.exception))

            lock_path.unlink()
            with mock.patch.object(
                config_store.os, "open", side_effect=PermissionError("blocked")
            ):
                with self.assertRaises(SystemExit) as raised:
                    with config_store.config_lock(
                        td, mock.Mock(), None, abort, time.time, lambda _: None
                    ):
                        self.fail("entered without a lock")
            expected = ("config is locked" if os.name == "nt"
                        else "cannot open config lock")
            self.assertIn(expected, str(raised.exception))

            lock_path.write_text("live", encoding="utf-8")
            with mock.patch.object(
                config_store.os.path, "getmtime",
                side_effect=OSError("metadata denied"),
            ):
                with self.assertRaises(SystemExit):
                    with config_store.config_lock(
                        td, mock.Mock(), None, abort,
                        lambda: fixed_now, lambda _: None,
                    ):
                        self.fail("entered with an unreadable live lock")

    def test_fallback_lock_retries_windows_permission_race_on_existing_lock(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".env.lock"
            lock_path.write_text("live", encoding="utf-8")
            fixed_now = os.path.getmtime(lock_path)
            real_open = os.open
            attempts = []

            def windows_open(path, flags, mode=0o777):
                attempts.append(path)
                if len(attempts) == 1:
                    raise PermissionError(13, "sharing violation", path)
                return real_open(path, flags, mode)

            def release_lock(_seconds):
                lock_path.unlink()

            with mock.patch.object(
                config_store.os, "open", side_effect=windows_open
            ):
                with config_store.config_lock(
                    td, mock.Mock(), None, abort,
                    lambda: fixed_now, release_lock,
                ):
                    self.assertTrue(lock_path.exists())

            self.assertEqual(len(attempts), 2)
            self.assertFalse(lock_path.exists())

    def test_windows_delete_pending_permission_race_is_still_contention(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            missing_lock = Path(td) / ".env.lock"
            error = PermissionError(13, "sharing violation", str(missing_lock))

            self.assertTrue(config_store._portable_lock_is_contended(
                error, str(missing_lock), "nt"))
            self.assertFalse(config_store._portable_lock_is_contended(
                error, str(missing_lock), "posix"))

    def test_fallback_lock_write_and_cleanup_errors_fail_closed(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".env.lock"
            with mock.patch.object(
                config_store.os, "write", side_effect=OSError("write denied")
            ):
                with self.assertRaises(SystemExit):
                    with config_store.config_lock(
                        td, mock.Mock(), None, abort, time.time, lambda _: None
                    ):
                        self.fail("entered without writing the ownership token")
            self.assertFalse(lock_path.exists())

            with mock.patch.object(
                config_store.os, "unlink", side_effect=OSError("unlink denied")
            ):
                with config_store.config_lock(
                    td, mock.Mock(), None, abort, time.time, lambda _: None
                ):
                    self.assertTrue(lock_path.exists())
            self.assertTrue(lock_path.exists())
            lock_path.unlink()

    def test_save_merges_deletes_deduplicates_and_preserves_unmanaged_lines(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text(
                "# keep\nA=old\nB=gone\nA=duplicate\nUNMANAGED\n",
                encoding="utf-8",
            )
            updates = {"A": "new", "B": None, "C": "three"}
            config_store.save_config_values(
                str(path), updates, passthrough_lock, abort
            )

            self.assertEqual(updates, {"A": "new", "B": None, "C": "three"})
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# keep\nA=new\nUNMANAGED\nC=three\n",
            )
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(list(Path(td).glob(".env.*")), [])

    def test_callable_updates_observe_state_written_inside_lock(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text("A=stale\n", encoding="utf-8")

            @contextlib.contextmanager
            def refresh_then_lock(_conf_dir):
                path.write_text("A=fresh\n", encoding="utf-8")
                yield

            config_store.save_config_values(
                str(path),
                lambda conf: {"A": conf["A"] + "-merged"},
                refresh_then_lock,
                abort,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "A=fresh-merged\n")

    def test_concurrent_writers_do_not_lose_independent_updates(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        try:
            import fcntl as fcntl_module
        except ImportError:
            fcntl_module = None
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"

            def lock_factory(conf_dir):
                return config_store.config_lock(
                    conf_dir, lambda: None, fcntl_module, abort,
                    time.time, time.sleep,
                )

            def write(index):
                config_store.save_config_values(
                    str(path), {f"K{index}": str(index)}, lock_factory, abort
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                tuple(pool.map(write, range(24)))

            conf = config_store.read_config_file(
                str(path), "ambient-codex", io.StringIO(), os.name
            )
            self.assertEqual(conf, {f"K{i}": str(i) for i in range(24)})

    def test_replace_failure_preserves_original_and_cleans_temp(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text("A=original\n", encoding="utf-8")
            with mock.patch.object(
                config_store.os, "replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaises(SystemExit) as raised:
                    config_store.save_config_values(
                        str(path), {"A": "new"}, passthrough_lock, abort
                    )

            self.assertIn("failed to write", str(raised.exception))
            self.assertEqual(path.read_text(encoding="utf-8"), "A=original\n")
            self.assertEqual(list(Path(td).glob(".env.*")), [])

            with mock.patch.object(
                config_store.os, "fdopen", side_effect=OSError("fdopen failed")
            ):
                with self.assertRaises(SystemExit):
                    config_store.save_config_values(
                        str(path), {"A": "new"}, passthrough_lock, abort
                    )
            self.assertEqual(list(Path(td).glob(".env.*")), [])

            real_unlink = os.unlink
            with mock.patch.object(
                config_store.os, "replace", side_effect=OSError("replace failed")
            ), mock.patch.object(
                config_store.os, "unlink", side_effect=OSError("unlink failed")
            ):
                with self.assertRaises(SystemExit):
                    config_store.save_config_values(
                        str(path), {"A": "new"}, passthrough_lock, abort
                    )
            leftovers = tuple(Path(td).glob(".env.*"))
            self.assertEqual(len(leftovers), 1)
            real_unlink(leftovers[0])

    def test_read_and_temp_creation_failures_are_user_facing(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text("A=original\n", encoding="utf-8")
            with mock.patch("builtins.open", side_effect=PermissionError("read denied")):
                with self.assertRaises(SystemExit) as raised:
                    config_store.save_config_values(
                        str(path), {"A": "new"}, passthrough_lock, abort
                    )
            self.assertIn("cannot read config for update", str(raised.exception))

            with mock.patch.object(
                config_store.tempfile, "mkstemp", side_effect=OSError("temp denied")
            ):
                with self.assertRaises(SystemExit) as raised:
                    config_store.save_config_values(
                        str(path), {"A": "new"}, passthrough_lock, abort
                    )
            self.assertIn("failed to write", str(raised.exception))

    def test_destination_permissions_are_enforced_before_unlock(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            events = mock.Mock()
            real_chmod = os.chmod

            @contextlib.contextmanager
            def observed_lock(_conf_dir):
                events("enter")
                try:
                    yield
                finally:
                    events("exit")

            def observed_chmod(target, mode):
                if str(target) == str(path):
                    events("chmod")
                return real_chmod(target, mode)

            with mock.patch.object(
                config_store.os, "chmod", side_effect=observed_chmod
            ):
                config_store.save_config_values(
                    str(path), {"A": "new"}, observed_lock, abort
                )

            self.assertEqual(
                events.call_args_list,
                [mock.call("enter"), mock.call("chmod"), mock.call("exit")],
            )

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_write_directory_and_destination_permission_failures_are_explicit(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            conf_dir = Path(td) / "state"
            conf_dir.mkdir(mode=0o755)
            path = conf_dir / "env"
            config_store.save_config_values(
                str(path), {"A": "new"}, passthrough_lock, abort
            )
            self.assertEqual(stat.S_IMODE(os.stat(conf_dir).st_mode), 0o700)

            with mock.patch.object(
                config_store.os, "makedirs", side_effect=OSError("create denied")
            ):
                with self.assertRaises(SystemExit) as raised:
                    config_store.save_config_values(
                        str(Path(td) / "blocked" / "env"), {"A": "new"},
                        passthrough_lock, abort,
                    )
            self.assertIn("cannot create", str(raised.exception))

            real_chmod = os.chmod
            def fail_destination_chmod(target, mode):
                if str(target) == str(path):
                    raise OSError("secure denied")
                return real_chmod(target, mode)

            with mock.patch.object(
                config_store.os, "chmod", side_effect=fail_destination_chmod
            ):
                with self.assertRaises(SystemExit) as raised:
                    config_store.save_config_values(
                        str(path), {"A": "again"}, passthrough_lock, abort
                    )
            self.assertIn("failed to secure", str(raised.exception))

    def test_internal_import_has_no_external_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.config_store"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeConfigWriteTests(unittest.TestCase):
    def test_facade_paths_fcntl_and_lock_binding_remain_patchable(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            facade = load_facade(base / "home")
            path = base / "state" / "env"
            used = mock.Mock()

            @contextlib.contextmanager
            def fake_lock(conf_dir):
                used(conf_dir)
                yield

            with mock.patch.object(facade, "CONFIG_PATH", str(path)), \
                 mock.patch.object(facade, "_config_lock", fake_lock):
                facade.save_config_values({"A": "1"})

            used.assert_called_once_with(str(path.parent))
            self.assertEqual(path.read_text(encoding="utf-8"), "A=1\n")

            with mock.patch.object(facade, "STATE_DIR", str(path.parent)), \
                 mock.patch.object(facade, "fcntl", None):
                with facade._config_lock(str(path.parent)):
                    self.assertTrue((path.parent / ".env.lock").exists())
            self.assertFalse((path.parent / ".env.lock").exists())


if __name__ == "__main__":
    unittest.main()
