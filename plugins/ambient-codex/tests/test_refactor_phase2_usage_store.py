"""Phase 2D2A contracts for bounded private usage-ledger persistence."""

import contextlib
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
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = ("spool_line", "merge_spools", "append_line")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2d2a", str(BIN))
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


@contextlib.contextmanager
def granting_lock(path, wait_s):
    yield True


@contextlib.contextmanager
def denying_lock(path, wait_s):
    yield False


def persist(store, line, ledger, *, max_bytes=5_000_000, trim_keep_lines=20_000,
            lock_wait_s=2.0, fs_lock=granting_lock, pid_alive=lambda pid: False,
            getpid=os.getpid):
    store.append_line(
        line,
        usage_path=str(ledger),
        max_bytes=max_bytes,
        trim_keep_lines=trim_keep_lines,
        lock_wait_s=lock_wait_s,
        private_dir=private_dir,
        fs_lock=fs_lock,
        pid_alive=pid_alive,
        getpid=getpid,
    )


class UsageStoreOwnershipTests(unittest.TestCase):
    def test_module_owns_exact_exports(self):
        store = importlib.import_module("ambient_codex.usage_store")
        self.assertEqual(store.__all__, MOVED_NAMES)

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
                [sys.executable, "-c", "import ambient_codex.usage_store"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class UsageSpoolTests(unittest.TestCase):
    def test_spool_writes_a_private_per_pid_line(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            store.spool_line("A\n", str(ledger), 5_000_000, getpid=lambda: 4242)
            spool = Path(f"{ledger}.spool.4242")
            self.assertEqual(spool.read_text(encoding="utf-8"), "A\n")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(spool.stat().st_mode), 0o600)

    def test_spool_is_capped_and_drops_rather_than_grows(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            spool = Path(f"{ledger}.spool.4242")
            spool.write_text("x" * 50, encoding="utf-8")
            store.spool_line("MORE\n", str(ledger), 10, getpid=lambda: 4242)
            self.assertEqual(spool.read_text(encoding="utf-8"), "x" * 50)

    def test_default_getpid_is_resolved_lazily_at_call_time(self):
        # Parity guard: the original persistence called os.getpid() at runtime,
        # so a monkeypatch of os.getpid must still be honored by the default
        # (non-injected) path. An early-bound default argument would not be.
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            with mock.patch.object(store.os, "getpid", return_value=313131):
                store.spool_line("A\n", str(ledger), 5_000_000)
            self.assertTrue(Path(f"{ledger}.spool.313131").exists())

    def test_default_getpid_is_resolved_lazily_in_merge(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            own = Path(f"{ledger}.spool.424243")
            own.write_text("OWN\n", encoding="utf-8")
            # With current pid patched to match the spool, it merges as "own".
            with mock.patch.object(store.os, "getpid", return_value=424243):
                store.merge_spools(str(ledger), lambda pid: None)
            self.assertEqual(ledger.read_text(encoding="utf-8"), "OWN\n")
            self.assertFalse(own.exists())

    def test_spool_swallows_oserror(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with mock.patch.object(store.os, "open", side_effect=OSError("denied")):
            store.spool_line("A\n", "/nonexistent-xyz/usage.jsonl", 10,
                             getpid=lambda: 1)


class UsageMergeTests(unittest.TestCase):
    def test_dead_and_own_merge_while_live_is_preserved(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            own = Path(f"{ledger}.spool.777")
            dead = Path(f"{ledger}.spool.999992")
            live = Path(f"{ledger}.spool.999991")
            own.write_text("OWN\n", encoding="utf-8")
            dead.write_text("DEAD\n", encoding="utf-8")
            live.write_text("LIVE\n", encoding="utf-8")
            liveness = {999991: True, 999992: False}
            store.merge_spools(str(ledger), lambda pid: liveness.get(pid),
                               getpid=lambda: 777)
            body = ledger.read_text(encoding="utf-8")
            self.assertIn("OWN\n", body)
            self.assertIn("DEAD\n", body)
            self.assertNotIn("LIVE\n", body)
            self.assertFalse(own.exists())
            self.assertFalse(dead.exists())
            self.assertTrue(live.exists())

    def test_unknown_liveness_preserves_foreign_spool(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            foreign = Path(f"{ledger}.spool.424242")
            foreign.write_text("FOREIGN\n", encoding="utf-8")
            store.merge_spools(str(ledger), lambda pid: None, getpid=lambda: 1)
            self.assertTrue(foreign.exists())
            self.assertEqual(ledger.read_text(encoding="utf-8"), "")

    def test_malformed_spool_name_is_skipped(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            junk = Path(f"{ledger}.spool.notapid")
            junk.write_text("JUNK\n", encoding="utf-8")
            store.merge_spools(str(ledger), lambda pid: False, getpid=lambda: 1)
            self.assertTrue(junk.exists())
            self.assertEqual(ledger.read_text(encoding="utf-8"), "")

    def test_missing_directory_is_a_noop(self):
        store = importlib.import_module("ambient_codex.usage_store")
        store.merge_spools("/nonexistent-xyz/usage.jsonl",
                           lambda pid: False, getpid=lambda: 1)


class UsageAppendTests(unittest.TestCase):
    def test_appends_line_and_creates_private_ledger(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            persist(store, '{"x":1}\n', ledger)
            self.assertEqual(ledger.read_text(encoding="utf-8"), '{"x":1}\n')
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)

    def test_trims_to_newest_lines_past_cap_then_appends(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            private_dir(str(ledger.parent))
            ledger.write_text("".join(f"L{i}\n" for i in range(10)),
                              encoding="utf-8")
            persist(store, "NEW\n", ledger, max_bytes=10, trim_keep_lines=2)
            self.assertEqual(
                ledger.read_text(encoding="utf-8").splitlines(),
                ["L8", "L9", "NEW"],
            )

    def test_heals_group_or_world_readable_ledger(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions only")
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "conf"
            private_dir(str(conf))
            ledger = conf / "usage.jsonl"
            ledger.write_text("OLD\n", encoding="utf-8")
            os.chmod(ledger, 0o644)
            persist(store, "NEW\n", ledger)
            self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)

    def test_spools_when_the_file_lock_is_unavailable(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            persist(store, "LINE\n", ledger, fs_lock=denying_lock,
                    getpid=lambda: 555)
            self.assertFalse(ledger.exists())
            spool = Path(f"{ledger}.spool.555")
            self.assertEqual(spool.read_text(encoding="utf-8"), "LINE\n")

    def test_merges_spools_before_the_trim(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "conf"
            private_dir(str(conf))
            ledger = conf / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            dead = Path(f"{ledger}.spool.999992")
            dead.write_text("SPOOLED\n", encoding="utf-8")
            persist(store, "LIVEADD\n", ledger, pid_alive=lambda pid: False)
            body = ledger.read_text(encoding="utf-8")
            self.assertEqual(body, "SPOOLED\nLIVEADD\n")
            self.assertFalse(dead.exists())

    def test_best_effort_when_private_dir_raises(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"

            def boom(path):
                raise OSError("denied")

            store.append_line(
                "X\n", usage_path=str(ledger), max_bytes=10,
                trim_keep_lines=5, lock_wait_s=1.0, private_dir=boom,
                fs_lock=granting_lock, pid_alive=lambda pid: False,
            )
            self.assertFalse(ledger.exists())

    def test_in_process_appends_are_never_torn(self):
        store = importlib.import_module("ambient_codex.usage_store")
        line = json.dumps({"v": "Z" * 500}) + "\n"
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            private_dir(str(ledger.parent))

            def writer():
                for _ in range(50):
                    persist(store, line, ledger)

            threads = [threading.Thread(target=writer) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            records = ledger.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 6 * 50)
            for record in records:
                self.assertEqual(json.loads(record), {"v": "Z" * 500})


class UsageFacadeTests(unittest.TestCase):
    def test_facade_delegates_persistence_with_patchable_knobs(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            facade.USAGE_PATH = "/patched/conf/usage.jsonl"
            facade.USAGE_MAX_BYTES = 123
            facade.USAGE_TRIM_KEEP_LINES = 7
            facade._LEDGER_LOCK_WAIT_S = 0.5
            with mock.patch.object(facade._usage_store, "append_line") as append:
                facade.log_usage(
                    "glm", {"prompt_tokens": 10, "completion_tokens": 3})
            self.assertEqual(append.call_count, 1)
            args, kwargs = append.call_args
            self.assertEqual(kwargs["usage_path"], "/patched/conf/usage.jsonl")
            self.assertEqual(kwargs["max_bytes"], 123)
            self.assertEqual(kwargs["trim_keep_lines"], 7)
            self.assertEqual(kwargs["lock_wait_s"], 0.5)
            self.assertIs(kwargs["private_dir"], facade._private_dir)
            self.assertIs(kwargs["fs_lock"], facade._fs_lock)
            self.assertIs(kwargs["pid_alive"], facade._pid_alive)
            record = json.loads(args[0])
            self.assertEqual(record["model"], "glm")
            self.assertEqual(record["in"], 10)
            self.assertEqual(record["out"], 3)
            self.assertIn("ts", record)

    def test_facade_record_carries_char_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            captured = {}

            def capture(line, **kwargs):
                captured["line"] = line

            with mock.patch.object(
                    facade._usage_store, "append_line", side_effect=capture):
                facade.log_usage(
                    "glm", {"prompt_tokens": 100, "completion_tokens": 50},
                    input_chars=400)
            record = json.loads(captured["line"])
            self.assertEqual(record.get("chars"), 400)

    def test_facade_no_longer_defines_moved_persistence_helpers(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            self.assertFalse(hasattr(facade, "_spool_usage_line"))
            self.assertFalse(hasattr(facade, "_merge_usage_spools"))


class UsageHardeningTests(unittest.TestCase):
    def test_append_line_default_getpid_is_lazy(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            with mock.patch.object(store.os, "getpid", return_value=767676):
                store.append_line(
                    "L\n", usage_path=str(ledger), max_bytes=10,
                    trim_keep_lines=5, lock_wait_s=1.0, private_dir=private_dir,
                    fs_lock=denying_lock, pid_alive=lambda pid: False)
            self.assertTrue(Path(f"{ledger}.spool.767676").exists())
            self.assertFalse(ledger.exists())

    def test_serialize_lock_wraps_fs_lock_and_denied_lock_never_appends(self):
        store = importlib.import_module("ambient_codex.usage_store")
        events = []

        class Recorder:
            def __enter__(self):
                events.append("enter:serial")
                return self

            def __exit__(self, *exc):
                events.append("exit:serial")
                return False

        @contextlib.contextmanager
        def recording_fs_lock(path, wait_s):
            events.append("enter:fs")
            try:
                yield False  # deny the lock -> spool path
            finally:
                events.append("exit:fs")

        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "conf" / "usage.jsonl"
            with mock.patch.object(store, "_LEDGER_SERIALIZE", Recorder()):
                store.append_line(
                    "L\n", usage_path=str(ledger), max_bytes=1_000,
                    trim_keep_lines=5, lock_wait_s=1.0, private_dir=private_dir,
                    fs_lock=recording_fs_lock, pid_alive=lambda pid: False,
                    getpid=lambda: 4242)
            self.assertEqual(
                events,
                ["enter:serial", "enter:fs", "exit:fs", "exit:serial"],
            )
            self.assertFalse(ledger.exists())
            self.assertTrue(Path(f"{ledger}.spool.4242").exists())

    def test_corrupt_utf8_ledger_does_not_raise(self):
        store = importlib.import_module("ambient_codex.usage_store")
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "conf"
            private_dir(str(conf))
            ledger = conf / "usage.jsonl"
            ledger.write_bytes(b"\xff\xfe not-utf8 junk\n" * 20)
            persist(store, "NEW\n", ledger, max_bytes=10, trim_keep_lines=5)
            # read as text so the check is newline-agnostic (Windows appends
            # "NEW\r\n" in text mode); errors="replace" tolerates the corrupt head.
            tail = ledger.read_text(encoding="utf-8", errors="replace")
            self.assertTrue(tail.endswith("NEW\n"))

    def test_out_of_range_pid_spool_is_skipped_without_probing_liveness(self):
        store = importlib.import_module("ambient_codex.usage_store")
        probed = []
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            huge = Path(f"{ledger}.spool.99999999999999999999")
            huge.write_text("HUGE\n", encoding="utf-8")

            def probe(pid):
                probed.append(pid)
                return False

            store.merge_spools(str(ledger), probe, getpid=lambda: 1)
            self.assertEqual(probed, [])
            self.assertTrue(huge.exists())
            self.assertEqual(ledger.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
