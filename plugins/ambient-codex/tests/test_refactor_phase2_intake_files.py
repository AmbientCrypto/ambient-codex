"""Phase 2C2A contracts for bounded explicit-file intake."""

import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = (
    "read_files",
    "read_map_item",
    "stdin_wait_seconds",
    "read_stdin_text",
    "read_stdin_bounded",
    "stdin_ready",
    "stdin_has_waiting_data",
)


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c2a", str(BIN))
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


class InternalBatchFileTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        intake = importlib.import_module("ambient_codex.intake")

        self.assertEqual(intake.__all__, MOVED_NAMES)

    def test_regular_text_is_lossy_decoded_into_immutable_results(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "odd.txt"
            path.write_bytes(b"caf\xe9\n")

            chunks, warnings, overflow = intake.read_files((str(path),), 100)

        self.assertEqual(chunks, ((str(path), "caf\ufffd\n"),))
        self.assertEqual(warnings, ())
        self.assertIsNone(overflow)
        self.assertIsInstance(chunks, tuple)

    def test_open_uses_nonfollowing_nonblocking_descriptor_flags(self):
        intake = importlib.import_module("ambient_codex.intake")
        real_open = os.open
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regular.txt"
            path.write_text("safe", encoding="utf-8")
            with mock.patch.object(intake.os, "open", wraps=real_open) as opener:
                chunks, warnings, overflow = intake.read_files((str(path),), 100)

        self.assertEqual(chunks, ((str(path), "safe"),))
        self.assertEqual(warnings, ())
        self.assertIsNone(overflow)
        flags = opener.call_args.args[1]
        if hasattr(os, "O_NOFOLLOW"):
            self.assertTrue(flags & os.O_NOFOLLOW)
        if hasattr(os, "O_NONBLOCK"):
            self.assertTrue(flags & os.O_NONBLOCK)

    def test_open_failure_is_a_warning_or_per_item_error(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regular.txt"
            path.write_text("safe", encoding="utf-8")
            denied = PermissionError(13, "denied after type check")
            with mock.patch.object(intake.os, "open", side_effect=denied):
                chunks, warnings, overflow = intake.read_files((str(path),), 100)
                map_text, map_error = intake.read_map_item(str(path), 100)

        self.assertEqual(chunks, ())
        self.assertEqual(len(warnings), 1)
        self.assertIn("denied after type check", warnings[0])
        self.assertIsNone(overflow)
        self.assertIsNone(map_text)
        self.assertIn("unreadable", map_error)

    def test_open_descriptor_is_revalidated_as_regular(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "swapped.txt"
            path.write_text("safe", encoding="utf-8")
            opened = mock.Mock(st_mode=stat.S_IFIFO)
            with mock.patch.object(intake.os, "fstat", return_value=opened):
                chunks, warnings, overflow = intake.read_files((str(path),), 100)

        self.assertEqual(chunks, ())
        self.assertEqual(warnings, (f"skipping {path} (not a regular file)",))
        self.assertIsNone(overflow)

    def test_descriptor_and_stream_read_errors_remain_bounded(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regular.txt"
            path.write_text("safe", encoding="utf-8")
            with mock.patch.object(
                    intake.os, "fstat", side_effect=OSError("fstat failed")
            ):
                _chunks, fstat_warnings, _overflow = intake.read_files(
                    (str(path),), 100
                )
            with mock.patch.object(
                    intake.os, "fdopen", side_effect=OSError("fdopen failed")
            ):
                _chunks, fdopen_warnings, _overflow = intake.read_files(
                    (str(path),), 100
                )

            class FailingSource:
                def __init__(self, descriptor):
                    self.descriptor = descriptor

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    os.close(self.descriptor)

                def read(self, _limit):
                    raise OSError("read failed")

            def failing_source(descriptor, *_args, **_kwargs):
                return FailingSource(descriptor)

            with mock.patch.object(intake.os, "fdopen", side_effect=failing_source):
                _chunks, read_warnings, _overflow = intake.read_files(
                    (str(path),), 100
                )

        self.assertIn("fstat failed", fstat_warnings[0])
        self.assertIn("fdopen failed", fdopen_warnings[0])
        self.assertIn("read failed", read_warnings[0])

    def test_nonregular_missing_empty_and_binary_inputs_are_bounded(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "directory"
            directory.mkdir()
            target = root / "target.txt"
            target.write_text("target", encoding="utf-8")
            symlink = root / "link.txt"
            symlink.symlink_to(target)
            empty = root / "empty.txt"
            empty.write_text(" \n", encoding="utf-8")
            binary = root / "binary.dat"
            binary.write_bytes(b"text-prefix" + b"\x00" + b"tail")
            missing = root / "missing.txt"
            paths = tuple(map(str, (directory, symlink, missing, empty, binary)))

            started = time.monotonic()
            chunks, warnings, overflow = intake.read_files(paths, 100)

        self.assertEqual(chunks, ())
        self.assertIsNone(overflow)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(warnings), 5)
        self.assertIn(f"skipping {directory} (not a regular file)", warnings)
        self.assertIn(f"skipping {symlink} (not a regular file)", warnings)
        self.assertTrue(any(f"skipping {missing} (" in item for item in warnings))
        self.assertIn(f"skipping {empty} (empty)", warnings)
        self.assertIn(f"skipping {binary} (looks binary)", warnings)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is POSIX-only")
    def test_fifo_is_classified_without_opening_or_hanging(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pipe"
            os.mkfifo(path)
            started = time.monotonic()

            chunks, warnings, overflow = intake.read_files((str(path),), 100)

        self.assertEqual(chunks, ())
        self.assertEqual(warnings, (f"skipping {path} (not a regular file)",))
        self.assertIsNone(overflow)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_multibyte_files_use_a_cumulative_character_ceiling(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            first = Path(td) / "first.txt"
            second = Path(td) / "second.txt"
            first.write_text("\u914d" * 40, encoding="utf-8")
            second.write_text("\u7f6e" * 60, encoding="utf-8")

            chunks, warnings, overflow = intake.read_files(
                (str(first), str(second)), 100
            )

        self.assertEqual(tuple(len(text) for _path, text in chunks), (40, 60))
        self.assertEqual(warnings, ())
        self.assertIsNone(overflow)

    def test_overflow_identifies_the_first_path_without_returning_truncation(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            first = Path(td) / "first.txt"
            second = Path(td) / "second.txt"
            first.write_text("a" * 60, encoding="utf-8")
            second.write_text("b" * 41, encoding="utf-8")

            chunks, warnings, overflow = intake.read_files(
                (str(first), str(second)), 100
            )

        self.assertEqual(chunks, ((str(first), "a" * 60),))
        self.assertEqual(warnings, ())
        self.assertEqual(overflow, str(second))

    def test_invalid_character_ceiling_fails_fast(self):
        intake = importlib.import_module("ambient_codex.intake")

        for invalid in (0, -1, 1.5, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                intake.read_files((), invalid)


class InternalMapFileTests(unittest.TestCase):
    def test_multibyte_item_below_character_ceiling_is_complete(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cjk.txt"
            content = "\u914d" * 50
            path.write_text(content, encoding="utf-8")

            text, error = intake.read_map_item(str(path), 100)

        self.assertEqual(text, content)
        self.assertIsNone(error)

    def test_decoded_item_above_character_ceiling_is_refused(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "large.txt"
            path.write_text("\u914d" * 101, encoding="utf-8")

            text, error = intake.read_map_item(str(path), 100)

        self.assertIsNone(text)
        self.assertIn("file exceeds the 100-char ceiling", error)

    def test_nul_after_old_prefix_boundary_is_still_binary(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "late-nul.dat"
            path.write_bytes(b"a" * 9_000 + b"\x00tail")

            text, error = intake.read_map_item(str(path), 20_000)

        self.assertIsNone(text)
        self.assertEqual(error, "looks binary \u2014 map sends text items only")

    def test_map_errors_remain_per_item_and_specific(self):
        intake = importlib.import_module("ambient_codex.intake")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "folder"
            directory.mkdir()
            target = root / "target.txt"
            target.write_text("target", encoding="utf-8")
            symlink = root / "link.txt"
            symlink.symlink_to(target)
            empty = root / "empty.txt"
            empty.write_text("\n", encoding="utf-8")
            missing = root / "missing.txt"

            cases = (
                (directory, "is a directory \u2014 map takes one FILE per item"),
                (symlink, "not a regular file"),
                (empty, "empty file"),
                (missing, "unreadable ("),
            )
            for path, expected in cases:
                with self.subTest(path=path):
                    text, error = intake.read_map_item(str(path), 100)
                    self.assertIsNone(text)
                    self.assertIn(expected, error)


class IntakeImportTests(unittest.TestCase):
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
                [sys.executable, "-c", "import ambient_codex.intake"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeFileIntakeTests(unittest.TestCase):
    def test_batch_wrapper_preserves_messages_exit_category_and_patchable_cap(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            core_result = (
                (("good.txt", "ok"),),
                ("skipping missing.txt (gone)",),
                "large.txt",
            )
            err = io.StringIO()
            with mock.patch.object(
                    facade._intake_core, "read_files", return_value=core_result
            ) as read_core, mock.patch.object(facade, "ABS_MAX_CHARS", 321), \
                    mock.patch.object(facade, "_argv_command", return_value="audit"), \
                    mock.patch.object(facade, "_fail_exit") as fail, \
                    contextlib.redirect_stderr(err):
                chunks = facade.read_files(["good.txt", "large.txt"])

            read_core.assert_called_once_with(("good.txt", "large.txt"), 321)
            self.assertEqual(chunks, [("good.txt", "ok")])
            self.assertEqual(err.getvalue(), "ambient: skipping missing.txt (gone)\n")
            args = fail.call_args.args
            self.assertEqual(args[1:3], ("audit", "input"))
            self.assertIn("input exceeds 321 chars at large.txt", args[3])

    def test_map_wrapper_retains_name_and_patchable_cap(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._intake_core,
                    "read_map_item",
                    return_value=("body", None),
            ) as read_core, mock.patch.object(facade, "ABS_MAX_CHARS", 654):
                result = facade._read_map_item("item.txt")

            self.assertEqual(result, ("body", None))
            read_core.assert_called_once_with("item.txt", 654)


if __name__ == "__main__":
    unittest.main()
