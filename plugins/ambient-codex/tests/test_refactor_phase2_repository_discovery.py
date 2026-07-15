"""Phase 2C3B contracts for safe repository discovery and classification."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
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
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c3b", str(BIN))
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


def git_result(stdout, returncode=0):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=b"")


class CandidatePathTests(unittest.TestCase):
    def test_module_owns_exact_exports_and_immutable_skip_record(self):
        repository = importlib.import_module("ambient_codex.repository")

        self.assertEqual(repository.__all__, MOVED_NAMES)
        skipped = repository.RepositorySkips()
        self.assertEqual(
            tuple(skipped),
            (0, 0, 0, (), 0, 0),
        )
        with self.assertRaises(AttributeError):
            skipped.binary = 1

    def test_git_lane_uses_binary_nul_framing_and_injected_runner(self):
        repository = importlib.import_module("ambient_codex.repository")
        calls = []

        def run_git(command, **kwargs):
            calls.append((command, kwargs))
            return git_result("src/a.py\0space name.py\0".encode("utf-8"))

        paths, used_git = repository.candidate_paths(
            "/repo",
            run_git,
            subprocess.TimeoutExpired,
            frozenset({"vendor"}),
        )

        self.assertEqual(paths, ("src/a.py", "space name.py"))
        self.assertTrue(used_git)
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(
            command,
            [
                "git", "-c", "core.fsmonitor=false", "--no-pager", "-C",
                "/repo", "ls-files", "-z", "--cached", "--others",
                "--exclude-standard",
            ],
        )
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["env"]["GIT_PAGER"], "")

    @unittest.skipIf(os.name == "nt", "arbitrary filename bytes are POSIX-only")
    def test_git_lane_preserves_non_utf8_posix_filename_bytes(self):
        repository = importlib.import_module("ambient_codex.repository")

        paths, used_git = repository.candidate_paths(
            "/repo",
            lambda *_args, **_kwargs: git_result(b"bad-\xff.py\0"),
            subprocess.TimeoutExpired,
            frozenset(),
        )

        self.assertTrue(used_git)
        self.assertEqual(len(paths), 1)
        self.assertEqual(os.fsencode(paths[0]), b"bad-\xff.py")

    @unittest.skipUnless(shutil.which("git"), "Git executable unavailable")
    def test_real_git_integration_honors_ignore_and_returns_nul_paths(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(
                ["git", "init", "-q", str(root)],
                capture_output=True, check=True, timeout=30,
            )
            (root / ".gitignore").write_bytes(b"*.tmp\n")
            (root / "tracked.py").write_bytes(b"tracked\n")
            (root / "untracked.py").write_bytes(b"untracked\n")
            (root / "ignored.tmp").write_bytes(b"ignored\n")
            subprocess.run(
                ["git", "-C", str(root), "add", ".gitignore", "tracked.py"],
                capture_output=True, check=True, timeout=30,
            )

            paths, used_git = repository.candidate_paths(
                str(root), subprocess.run, subprocess.TimeoutExpired,
                frozenset({".git"}),
            )

        self.assertTrue(used_git)
        self.assertEqual(set(paths), {".gitignore", "tracked.py", "untracked.py"})

    def test_failed_or_malformed_git_falls_back_to_pruned_plain_walk(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as outside_td:
            root = Path(td)
            for relative in (
                "a.py",
                "src/b.py",
                "src/deep/c.py",
                "z/d.py",
                "node_modules/dep.js",
                ".hidden/secret.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x\n")
            outside = Path(outside_td)
            (outside / "leak.py").write_bytes(b"leak\n")
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                link = None
            expected = (
                "a.py",
                os.path.join("src", "b.py"),
                os.path.join("src", "deep", "c.py"),
                os.path.join("z", "d.py"),
            )

            failures = (
                lambda *_a, **_k: git_result(b"ignored\0", returncode=1),
                lambda *_a, **_k: git_result(object()),
            )
            for run_git in failures:
                with self.subTest(run_git=run_git):
                    paths, used_git = repository.candidate_paths(
                        str(root), run_git, subprocess.TimeoutExpired,
                        frozenset({"node_modules"}),
                    )
                    self.assertEqual(paths, expected)
                    self.assertFalse(used_git)
                    if link is not None:
                        self.assertFalse(any("leak.py" in path for path in paths))

            def unavailable(*_args, **_kwargs):
                raise OSError("git unavailable")

            paths, used_git = repository.candidate_paths(
                str(root), unavailable, subprocess.TimeoutExpired,
                frozenset({"node_modules"}),
            )
            self.assertEqual(paths, expected)
            self.assertFalse(used_git)

            def timed_out(*_args, **_kwargs):
                raise subprocess.TimeoutExpired("git", 30)

            def invalid_invocation(*_args, **_kwargs):
                raise ValueError("invalid subprocess input")

            for run_git in (timed_out, invalid_invocation):
                paths, used_git = repository.candidate_paths(
                    str(root), run_git, subprocess.TimeoutExpired,
                    frozenset({"node_modules"}),
                )
                self.assertEqual(paths, expected)
                self.assertFalse(used_git)


class RepositoryClassificationTests(unittest.TestCase):
    def test_stable_files_are_sorted_deduplicated_and_immutable(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "b.py").write_bytes(b"b\n")
            (root / "a.py").write_bytes(b"a\n")

            files, skipped = repository.classify_repository_files(
                str(root),
                (os.path.join("src", "b.py"), "a.py", "a.py"),
                False,
                100,
                frozenset(),
                frozenset(),
            )

        self.assertIsInstance(files, tuple)
        self.assertEqual([item[0] for item in files], ["a.py", "src/b.py"])
        self.assertEqual([item[2] for item in files], [2, 2])
        self.assertEqual(skipped, repository.RepositorySkips())

    def test_classifies_vendored_dot_lock_empty_binary_and_oversize_paths(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content = {
                "good.py": b"ok\n",
                ".github/workflow.yml": b"ci\n",
                "vendor/dep.py": b"dep\n",
                "package-lock.json": b"lock\n",
                "custom.lock": b"lock\n",
                "empty.py": b"",
                "binary.dat": b"x\x00y",
                "huge.py": b"0123456789",
            }
            for relative, body in content.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)

            files, skipped = repository.classify_repository_files(
                str(root),
                tuple(reversed(tuple(content))) + ("../escape.py", "/abs.py"),
                True,
                8,
                frozenset({"vendor"}),
                frozenset({"package-lock.json"}),
            )

            plain_files, plain_skipped = repository.classify_repository_files(
                str(root),
                (".github/workflow.yml",),
                False,
                8,
                frozenset({"vendor"}),
                frozenset(),
            )

        self.assertEqual([item[0] for item in files], [".github/workflow.yml", "good.py"])
        self.assertEqual(skipped.binary, 1)
        self.assertEqual(skipped.lockfile, 2)
        self.assertEqual(skipped.oversize, 1)
        self.assertEqual(skipped.oversize_paths, ("huge.py",))
        self.assertEqual(skipped.vendored, 1)
        self.assertEqual(plain_files, ())
        self.assertEqual(plain_skipped.vendored, 1)

    def test_oversize_evidence_is_capped_but_count_is_complete(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = tuple("file-{0:02}.py".format(index) for index in range(45))
            for name in names:
                (root / name).write_bytes(b"xx")

            files, skipped = repository.classify_repository_files(
                str(root), names, True, 1, frozenset(), frozenset(),
            )

        self.assertEqual(files, ())
        self.assertEqual(skipped.oversize, 45)
        self.assertEqual(skipped.oversize_paths, names[:40])

    def test_symlink_leaf_and_intermediate_escape_are_nonregular(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td:
            root = Path(td)
            outside = Path(outside_td)
            (outside / "leak.py").write_bytes(b"leak\n")
            leaf = root / "leaf.py"
            directory_link = root / "linked"
            try:
                leaf.symlink_to(outside / "leak.py")
                directory_link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest("symlink creation unavailable: {0}".format(error))

            files, skipped = repository.classify_repository_files(
                str(root),
                ("leaf.py", os.path.join("linked", "leak.py")),
                True,
                100,
                frozenset(),
                frozenset(),
            )

        self.assertEqual(files, ())
        self.assertEqual(skipped.nonregular, 2)

    def test_descriptor_identity_blocks_path_swap_before_binary_read(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_read = os.read
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "inside.py"
            outside = root / "outside.py"
            inside.write_bytes(b"inside\n")
            outside.write_bytes(b"outside\n")
            outside_descriptor = os.open(str(outside), os.O_RDONLY)
            with mock.patch.object(
                    repository.os, "open", return_value=outside_descriptor
            ), mock.patch.object(
                    repository.os, "read", wraps=real_read
            ) as reader:
                files, skipped = repository.classify_repository_files(
                    str(root), ("inside.py",), True, 100,
                    frozenset(), frozenset(),
                )

        self.assertEqual(files, ())
        self.assertEqual(skipped.nonregular, 1)
        reader.assert_not_called()

    def test_descriptor_fstat_failure_closes_and_omits(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_close = os.close
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "source.py"
            path.write_bytes(b"x\n")
            descriptor = os.open(str(path), os.O_RDONLY)
            try:
                with mock.patch.object(
                        repository.os, "open", return_value=descriptor
                ), mock.patch.object(
                        repository.os, "fstat", side_effect=OSError("fstat failed")
                ), mock.patch.object(
                        repository.os, "close", wraps=real_close
                ) as close:
                    files, skipped = repository.classify_repository_files(
                        str(root), ("source.py",), True, 100,
                        frozenset(), frozenset(),
                    )

                self.assertEqual(files, ())
                self.assertEqual(skipped, repository.RepositorySkips())
                close.assert_called_once_with(descriptor)
            finally:
                try:
                    real_close(descriptor)
                except OSError:
                    pass

    def test_binary_sniff_is_bounded_and_uses_safe_descriptor_flags(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_open = os.open
        real_read = os.read
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "source.py"
            path.write_bytes(b"x" * 10_000)
            with mock.patch.object(
                    repository.os, "open", wraps=real_open
            ) as opener, mock.patch.object(
                    repository.os, "read", wraps=real_read
            ) as reader:
                files, skipped = repository.classify_repository_files(
                    str(root), ("source.py",), True, 20_000,
                    frozenset(), frozenset(),
                )

        self.assertEqual([item[0] for item in files], ["source.py"])
        self.assertEqual(skipped, repository.RepositorySkips())
        self.assertEqual(reader.call_args.args[1], 8_192)
        flags = opener.call_args.args[1]
        if hasattr(os, "O_NOFOLLOW"):
            self.assertTrue(flags & os.O_NOFOLLOW)
        if hasattr(os, "O_NONBLOCK"):
            self.assertTrue(flags & os.O_NONBLOCK)

    def test_descriptor_snapshot_size_detects_growth_before_open(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_open = os.open
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "growing.py"
            path.write_bytes(b"x")

            def grow_then_open(target, flags):
                Path(target).write_bytes(b"0123456789")
                return real_open(target, flags)

            with mock.patch.object(repository.os, "open", side_effect=grow_then_open):
                files, skipped = repository.classify_repository_files(
                    str(root), ("growing.py",), True, 5,
                    frozenset(), frozenset(),
                )

        self.assertEqual(files, ())
        self.assertEqual(skipped.oversize, 1)
        self.assertEqual(skipped.oversize_paths, ("growing.py",))

    def test_per_file_cap_validation_fails_fast(self):
        repository = importlib.import_module("ambient_codex.repository")
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                repository.classify_repository_files(
                    ".", (), False, invalid, frozenset(), frozenset(),
                )

    def test_invalid_candidate_object_is_ignored_without_side_effects(self):
        repository = importlib.import_module("ambient_codex.repository")

        files, skipped = repository.classify_repository_files(
            ".", (object(),), True, 100, frozenset(), frozenset(),
        )

        self.assertEqual(files, ())
        self.assertEqual(skipped, repository.RepositorySkips())

    def test_open_identity_read_and_close_failures_are_fail_closed(self):
        repository = importlib.import_module("ambient_codex.repository")
        real_close = os.close
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "source.py"
            path.write_bytes(b"source\n")
            arguments = (
                str(root), ("source.py",), True, 100,
                frozenset(), frozenset(),
            )

            with mock.patch.object(
                    repository.os, "open", side_effect=OSError("open failed")
            ):
                files, skipped = repository.classify_repository_files(*arguments)
            self.assertEqual(files, ())
            self.assertEqual(skipped, repository.RepositorySkips())

            with mock.patch.object(
                    repository.os.path,
                    "samestat",
                    side_effect=ValueError("identity unavailable"),
            ):
                files, skipped = repository.classify_repository_files(*arguments)
            self.assertEqual(files, ())
            self.assertEqual(skipped.nonregular, 1)

            for result in (OSError("read failed"), bytearray(b"source\n")):
                with self.subTest(read_result=result), mock.patch.object(
                        repository.os,
                        "read",
                        side_effect=result if isinstance(result, OSError) else None,
                        return_value=None if isinstance(result, OSError) else result,
                ):
                    files, skipped = repository.classify_repository_files(*arguments)
                self.assertEqual(files, ())
                self.assertEqual(skipped, repository.RepositorySkips())

            with mock.patch.object(
                    repository, "_within_root", side_effect=(True, False)
            ):
                files, skipped = repository.classify_repository_files(*arguments)
            self.assertEqual(files, ())
            self.assertEqual(skipped.nonregular, 1)

            def close_then_error(descriptor):
                real_close(descriptor)
                raise OSError("close failed")

            with mock.patch.object(
                    repository.os, "close", side_effect=close_then_error
            ):
                files, skipped = repository.classify_repository_files(*arguments)
            self.assertEqual([item[0] for item in files], ["source.py"])
            self.assertEqual(skipped, repository.RepositorySkips())


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

    def test_facade_preserves_patchable_candidate_and_walk_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._repository_core,
                    "candidate_paths",
                    return_value=(("b.py", "a.py"), True),
            ) as candidates:
                paths, used_git = facade._repo_candidate_paths("/repo")

            self.assertEqual(paths, ["b.py", "a.py"])
            self.assertTrue(used_git)
            candidates.assert_called_once_with(
                "/repo",
                facade.subprocess.run,
                facade.subprocess.TimeoutExpired,
                facade.REPO_SKIP_DIRS,
                popen=facade.subprocess.Popen,
            )

            skipped = facade._repository_core.RepositorySkips(
                binary=1,
                lockfile=2,
                oversize=3,
                oversize_paths=("huge.py",),
                nonregular=4,
                vendored=5,
            )
            with mock.patch.object(
                    facade, "_repo_candidate_paths", return_value=(["a.py"], False)
            ) as facade_candidates, mock.patch.object(
                    facade._repository_core,
                    "classify_repository_files",
                    return_value=((('a.py', '/repo/a.py', 4),), skipped),
            ) as classify:
                files, public_skipped, used_git = facade.repo_walk("/repo")

            self.assertEqual(files, [("a.py", "/repo/a.py", 4)])
            self.assertEqual(public_skipped, {
                "binary": 1,
                "lockfile": 2,
                "oversize": 3,
                "oversize_paths": ["huge.py"],
                "nonregular": 4,
                "vendored": 5,
            })
            self.assertFalse(used_git)
            facade_candidates.assert_called_once_with("/repo")
            classify.assert_called_once_with(
                "/repo",
                ("a.py",),
                False,
                facade.REPO_FILE_MAX_BYTES,
                facade.REPO_SKIP_DIRS,
                facade.REPO_LOCKFILES,
            )

    def test_facade_keeps_legacy_run_only_subprocess_double_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            subprocess_double = types.SimpleNamespace(
                run=mock.Mock(), TimeoutExpired=subprocess.TimeoutExpired,
            )
            with mock.patch.object(facade, "subprocess", subprocess_double), \
                    mock.patch.object(
                        facade._repository_core,
                        "candidate_paths",
                        return_value=((), False),
                    ) as candidates:
                self.assertEqual(facade._repo_candidate_paths("/repo"), ([], False))

            candidates.assert_called_once_with(
                "/repo",
                subprocess_double.run,
                subprocess_double.TimeoutExpired,
                facade.REPO_SKIP_DIRS,
                popen=None,
            )


if __name__ == "__main__":
    unittest.main()
