"""Phase 2C3A contracts for repository gutters and size accounting."""

import importlib
import importlib.machinery
import importlib.util
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
    "RepositorySkips",
    "GitDiffSnapshot",
    "GitDiffFailure",
    "candidate_paths",
    "classify_repository_files",
    "capture_git_diff",
    "with_line_gutters",
    "guttered_file_size",
)


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c3a", str(BIN))
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


class LineGutterTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        repository = importlib.import_module("ambient_codex.repository")

        self.assertEqual(repository.__all__, MOVED_NAMES)

    def test_numbering_width_and_trailing_line_match_existing_contract(self):
        repository = importlib.import_module("ambient_codex.repository")
        hundred = "\n".join(f"line-{index}" for index in range(100))
        labeled = (("small.py", "one\ntwo\n"), ("wide.py", hundred))

        result = repository.with_line_gutters(labeled)

        self.assertEqual(result[0][1], " 1| one\n 2| two\n 3| ")
        self.assertTrue(result[1][1].startswith("  1| line-0\n  2| line-1"))
        self.assertIn("100| line-99", result[1][1])
        self.assertIsInstance(result, tuple)
        self.assertEqual(labeled[0], ("small.py", "one\ntwo\n"))

    def test_empty_input_and_empty_text_are_deterministic(self):
        repository = importlib.import_module("ambient_codex.repository")

        self.assertEqual(repository.with_line_gutters(()), ())
        self.assertEqual(
            repository.with_line_gutters((("empty.py", ""),)),
            (("empty.py", " 1| "),),
        )


class GutteredSizeTests(unittest.TestCase):
    def test_ascii_size_matches_the_actual_guttered_text(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.py"
            text = "a\nb\n"
            path.write_bytes(text.encode("utf-8"))

            estimate = repository.guttered_file_size(str(path), path.stat().st_size)
            guttered = repository.with_line_gutters((("source.py", text),))[0][1]

        self.assertEqual(estimate, len(guttered))
        self.assertEqual(estimate, 16)

    def test_three_digit_width_and_multibyte_sizes_are_conservative(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            ascii_path = Path(td) / "hundred.py"
            ascii_text = "\n".join("x" for _ in range(100))
            ascii_path.write_bytes(ascii_text.encode("utf-8"))
            self.assertEqual(
                repository.guttered_file_size(
                    str(ascii_path), ascii_path.stat().st_size
                ),
                len(repository.with_line_gutters((("x", ascii_text),))[0][1]),
            )

            unicode_path = Path(td) / "unicode.py"
            unicode_text = "\u914d\n\u7f6e\n"
            unicode_path.write_text(unicode_text, encoding="utf-8")
            estimate = repository.guttered_file_size(
                str(unicode_path), unicode_path.stat().st_size
            )
            actual = len(repository.with_line_gutters(
                (("unicode.py", unicode_text),)
            )[0][1])

        self.assertGreaterEqual(estimate, actual)

    def test_crlf_size_matches_preserved_repository_text(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "windows.py"
            raw = b"one\r\ntwo\r\n"
            text = raw.decode("utf-8")
            path.write_bytes(raw)

            estimate = repository.guttered_file_size(str(path), len(raw))
            actual = len(repository.with_line_gutters((("windows.py", text),))[0][1])

        self.assertEqual(estimate, actual)

    def test_missing_directory_and_symlink_fall_back_to_snapshot_size(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target.py"
            target.write_text("x\n", encoding="utf-8")
            link = root / "link.py"
            link.symlink_to(target)
            directory = root / "folder"
            directory.mkdir()

            self.assertEqual(repository.guttered_file_size(
                str(root / "missing.py"), 17
            ), 17)
            self.assertEqual(repository.guttered_file_size(str(link), 17), 17)
            self.assertEqual(repository.guttered_file_size(str(directory), 17), 17)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is POSIX-only")
    def test_fifo_is_rejected_without_opening_or_hanging(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pipe"
            os.mkfifo(path)
            started = time.monotonic()

            estimate = repository.guttered_file_size(str(path), 23)

        self.assertEqual(estimate, 23)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_descriptor_flags_prevent_following_or_blocking_path_swaps(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_open = os.open
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.py"
            path.write_text("x\n", encoding="utf-8")
            with mock.patch.object(repository.os, "open", wraps=real_open) as opener:
                repository.guttered_file_size(str(path), path.stat().st_size)

        flags = opener.call_args.args[1]
        if hasattr(os, "O_NOFOLLOW"):
            self.assertTrue(flags & os.O_NOFOLLOW)
        if hasattr(os, "O_NONBLOCK"):
            self.assertTrue(flags & os.O_NONBLOCK)

    def test_growth_read_is_bounded_by_snapshot_plus_one(self):
        repository = importlib.import_module("ambient_codex.repository")
        requests = []

        def growing_read(_descriptor, limit):
            requests.append(limit)
            return (b"x\n" * (limit + 1))[:limit]

        with mock.patch.object(
                repository, "_open_regular_descriptor", return_value=(99, None)
        ), mock.patch.object(repository.os, "read", side_effect=growing_read), \
                mock.patch.object(repository.os, "close") as close:
            estimate = repository.guttered_file_size("growing.py", 10)

        self.assertEqual(sum(requests), 11)
        self.assertEqual(estimate, 35)
        close.assert_called_once_with(99)

    def test_descriptor_and_read_failures_fall_back_to_snapshot_size(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.py"
            path.write_text("x\n", encoding="utf-8")
            size = path.stat().st_size
            with mock.patch.object(
                    repository.os, "open", side_effect=OSError("open failed")
            ):
                self.assertEqual(repository.guttered_file_size(str(path), size), size)
            with mock.patch.object(
                    repository.os, "fstat", side_effect=OSError("fstat failed")
            ):
                self.assertEqual(repository.guttered_file_size(str(path), size), size)
            with mock.patch.object(
                    repository.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFIFO)
            ):
                self.assertEqual(repository.guttered_file_size(str(path), size), size)
            with mock.patch.object(
                    repository.os, "read", side_effect=OSError("read failed")
            ):
                self.assertEqual(repository.guttered_file_size(str(path), size), size)
            with mock.patch.object(
                    repository.os, "read", return_value=bytearray(b"x")
            ):
                self.assertEqual(repository.guttered_file_size(str(path), size), size)

            empty = Path(td) / "empty.py"
            empty.write_bytes(b"")
            self.assertEqual(repository.guttered_file_size(str(empty), 0), 4)

        with mock.patch.object(
                repository, "_open_regular_descriptor", return_value=(99, None)
        ), mock.patch.object(repository.os, "read", return_value=b""), \
                mock.patch.object(
                    repository.os, "close", side_effect=OSError("close failed")
                ):
            self.assertEqual(repository.guttered_file_size("x", 1), 5)

    def test_invalid_snapshot_size_fails_fast(self):
        repository = importlib.import_module("ambient_codex.repository")
        for invalid in (-1, 1.5, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                repository.guttered_file_size("x", invalid)


class RepositoryImportAndFacadeTests(unittest.TestCase):
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
                [sys.executable, "-c", "import ambient_codex.repository"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])

    def test_facade_wrappers_retain_names_lists_and_patchable_limits(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._repository_core,
                    "with_line_gutters",
                    return_value=(("a.py", " 1| x"),),
            ) as gutters:
                result = facade.with_line_gutters([("a.py", "x")])
            self.assertEqual(result, [("a.py", " 1| x")])
            gutters.assert_called_once_with((("a.py", "x"),))

            with mock.patch.object(
                    facade._repository_core, "guttered_file_size", return_value=77
            ) as size:
                self.assertEqual(facade._guttered_size("a.py", 10), 77)
            size.assert_called_once_with("a.py", 10)


if __name__ == "__main__":
    unittest.main()
