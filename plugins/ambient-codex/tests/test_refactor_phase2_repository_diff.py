"""Phase 2C3C contracts for bounded Git diff and changed-file intake."""

import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c3c", str(BIN))
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


def command_result(
        returncode=0,
        stdout=b"",
        stderr=b"",
        overflow=False,
        timed_out=False,
        launch_error=None,
        read_error=None,
):
    return types.SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        overflow=overflow,
        timed_out=timed_out,
        launch_error=launch_error,
        read_error=read_error,
    )


@contextlib.contextmanager
def changed_directory(path):
    prior = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prior)


def run_git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=True,
        timeout=30,
    )


class GitDiffRecordAndCommandTests(unittest.TestCase):
    def test_module_owns_exact_exports_and_immutable_records(self):
        repository = importlib.import_module("ambient_codex.repository")

        self.assertEqual(repository.__all__, MOVED_NAMES)
        snapshot = repository.GitDiffSnapshot(
            "diff", "/repo", (("a.py", "/repo/a.py"),), ("outside.py",),
        )
        failure = repository.GitDiffFailure("input", "bad diff", False)
        self.assertEqual(snapshot.changed_files[0][0], "a.py")
        self.assertEqual(tuple(failure), ("input", "bad diff", False))
        with self.assertRaises(AttributeError):
            snapshot.root = "/other"
        with self.assertRaises(AttributeError):
            failure.message = "changed"

    def test_staged_capture_uses_bounded_commands_and_nul_paths(self):
        repository = importlib.import_module("ambient_codex.repository")
        popen = object()
        root = os.path.realpath("/repo")
        responses = (
            command_result(stdout=b"true\n"),
            command_result(stdout=b"diff --git a/a.py b/a.py\n"),
            command_result(stdout=os.fsencode(root) + b"\n"),
            command_result(stdout=b"a.py\0space name.py\0"),
        )
        with mock.patch.object(
                repository, "_run_bounded_git", side_effect=responses, create=True
        ) as bounded:
            snapshot, failure = repository.capture_git_diff(
                True, None, popen, subprocess.TimeoutExpired, 20_000,
            )

        self.assertIsNone(failure)
        self.assertEqual(snapshot.diff_text, "diff --git a/a.py b/a.py\n")
        self.assertEqual(snapshot.root, root)
        self.assertEqual(snapshot.changed_files, (
            ("a.py", os.path.join(root, "a.py")),
            ("space name.py", os.path.join(root, "space name.py")),
        ))
        self.assertEqual(snapshot.omitted_paths, ())
        self.assertEqual(
            [call.args for call in bounded.call_args_list],
            [
                (
                    [
                        "git", "-c", "core.fsmonitor=false", "--no-pager",
                        "rev-parse", "--is-inside-work-tree",
                    ], popen,
                    subprocess.TimeoutExpired, 65_536, 4_096, 30,
                ),
                (
                    [
                        "git", "-c", "core.fsmonitor=false", "--no-pager",
                        "diff", "--no-ext-diff", "--no-textconv", "--cached", "--",
                    ], popen,
                    subprocess.TimeoutExpired, 20_000, 4_096, 30,
                ),
                (
                    [
                        "git", "-c", "core.fsmonitor=false", "--no-pager",
                        "rev-parse", "--show-toplevel",
                    ], popen,
                    subprocess.TimeoutExpired, 65_536, 4_096, 30,
                ),
                (
                    [
                        "git", "-c", "core.fsmonitor=false", "--no-pager",
                        "diff", "--no-ext-diff", "--no-textconv",
                        "--name-only", "-z", "--cached", "--",
                    ],
                    popen, subprocess.TimeoutExpired, 20_000, 4_096, 30,
                ),
            ],
        )

    def test_revision_capture_ends_option_parsing(self):
        repository = importlib.import_module("ambient_codex.repository")
        root = os.path.realpath("/repo")
        responses = (
            command_result(stdout=b"true\n"),
            command_result(stdout=b"patch\n"),
            command_result(stdout=os.fsencode(root) + b"\n"),
            command_result(stdout=b"a.py\0"),
        )
        with mock.patch.object(
                repository, "_run_bounded_git", side_effect=responses, create=True
        ) as bounded:
            snapshot, failure = repository.capture_git_diff(
                False, "main...HEAD", object(), subprocess.TimeoutExpired, 10_000,
            )

        self.assertIsNone(failure)
        self.assertIsNotNone(snapshot)
        self.assertEqual(
            bounded.call_args_list[1].args[0],
            [
                "git", "-c", "core.fsmonitor=false", "--no-pager",
                "diff", "--no-ext-diff", "--no-textconv", "main...HEAD", "--",
            ],
        )
        self.assertEqual(
            bounded.call_args_list[3].args[0],
            [
                "git", "-c", "core.fsmonitor=false", "--no-pager",
                "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                "main...HEAD", "--",
            ],
        )

    def test_invalid_revision_and_ceiling_fail_before_launch(self):
        repository = importlib.import_module("ambient_codex.repository")
        invalid_refs = (None, "", "--cached", "bad\nref", "bad\x00ref", "x" * 4_097)
        for ref in invalid_refs:
            with self.subTest(ref=ref), mock.patch.object(
                    repository, "_run_bounded_git", create=True
            ) as bounded:
                snapshot, failure = repository.capture_git_diff(
                    False, ref, object(), subprocess.TimeoutExpired, 10_000,
                )
                self.assertIsNone(snapshot)
                self.assertIsNotNone(failure)
                self.assertIn("revision", failure.message)
                bounded.assert_not_called()
        for cap in (0, -1, True, 1.5):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                repository.capture_git_diff(
                    True, None, object(), subprocess.TimeoutExpired, cap,
                )

    def test_command_failures_never_degrade_to_diff_only(self):
        repository = importlib.import_module("ambient_codex.repository")
        root = os.path.realpath("/repo")
        cases = (
            (
                (command_result(returncode=128, stderr=b"outside"),),
                "must run inside a git repository",
                True,
            ),
            (
                (
                    command_result(stdout=b"true\n"),
                    command_result(returncode=128, stderr=b"bad ref"),
                ),
                "git diff failed: bad ref",
                False,
            ),
            (
                (command_result(stdout=b"true\n"), command_result(stdout=b"")),
                "no changes to audit",
                True,
            ),
            (
                (
                    command_result(stdout=b"true\n"),
                    command_result(stdout=b"patch\n"),
                    command_result(returncode=128, stderr=b"no root"),
                ),
                "show-toplevel failed",
                False,
            ),
            (
                (
                    command_result(stdout=b"true\n"),
                    command_result(stdout=b"patch\n"),
                    command_result(stdout=os.fsencode(root) + b"\n"),
                    command_result(returncode=128, stderr=b"no paths"),
                ),
                "changed-path listing failed",
                False,
            ),
        )
        for responses, message, usage in cases:
            with self.subTest(message=message), mock.patch.object(
                    repository,
                    "_run_bounded_git",
                    side_effect=responses,
                    create=True,
            ):
                snapshot, failure = repository.capture_git_diff(
                    True, None, object(), subprocess.TimeoutExpired, 10_000,
                )
            self.assertIsNone(snapshot)
            self.assertIn(message, failure.message)
            self.assertEqual(failure.usage, usage)

    def test_launch_timeout_read_and_overflow_fail_explicitly(self):
        repository = importlib.import_module("ambient_codex.repository")
        failures = (
            (command_result(launch_error="git unavailable"), "unable to run git"),
            (command_result(timed_out=True), "timed out"),
            (command_result(read_error="broken pipe"), "cannot read git output"),
            (command_result(overflow=True), "exceeds"),
        )
        for result, message in failures:
            with self.subTest(message=message), mock.patch.object(
                    repository,
                    "_run_bounded_git",
                    return_value=result,
                    create=True,
            ):
                snapshot, failure = repository.capture_git_diff(
                    True, None, object(), subprocess.TimeoutExpired, 10_000,
                )
            self.assertIsNone(snapshot)
            self.assertIn(message, failure.message)

    def test_bounded_runner_kills_child_that_ignores_overflow_termination(self):
        repository = importlib.import_module("ambient_codex.repository")

        class FakeTimeout(Exception):
            pass

        class FakePipe:
            def __init__(self, payload):
                self.payload = payload
                self.closed = False

            def read(self, _limit):
                payload, self.payload = self.payload, b""
                return payload

            def close(self):
                self.closed = True

        class IgnoringProcess:
            def __init__(self):
                self.stdout = FakePipe(b"x" * 100)
                self.stderr = FakePipe(b"")
                self.returncode = None
                self.terminated = 0
                self.killed = 0
                self.signal = threading.Event()

            def terminate(self):
                self.terminated += 1
                self.signal.set()

            def kill(self):
                self.killed += 1
                self.returncode = -9
                self.signal.set()

            def wait(self, timeout):
                self.signal.wait(min(timeout, 0.5))
                if self.returncode is not None:
                    return self.returncode
                raise FakeTimeout()

        process = IgnoringProcess()
        started = time.monotonic()
        result = repository._run_bounded_git(
            ["git", "diff"],
            lambda *_args, **_kwargs: process,
            FakeTimeout,
            8,
            8,
            5,
        )

        self.assertTrue(result.overflow)
        self.assertEqual(result.stdout, b"x" * 8)
        self.assertEqual(result.overflow_cap, 8)
        self.assertEqual(result.overflow_stream, "stdout")
        self.assertGreaterEqual(process.terminated, 1)
        self.assertGreaterEqual(process.killed, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_bounded_runner_reports_launch_timeout_and_malformed_pipe(self):
        repository = importlib.import_module("ambient_codex.repository")

        class FakeTimeout(Exception):
            pass

        class FakePipe:
            def __init__(self, payload):
                self.payload = payload

            def read(self, _limit):
                payload, self.payload = self.payload, b""
                return payload

            def close(self):
                pass

        class FakeProcess:
            def __init__(self, stdout=b"", timeout=False):
                self.stdout = FakePipe(stdout)
                self.stderr = FakePipe(b"")
                self.returncode = None
                self.timeout = timeout

            def terminate(self):
                if not self.timeout:
                    self.returncode = -15

            def kill(self):
                self.returncode = -9

            def wait(self, timeout):
                if self.timeout and self.returncode is None:
                    raise FakeTimeout()
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

        launched = repository._run_bounded_git(
            ["git", "diff"],
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
            FakeTimeout,
            10,
            10,
            1,
        )
        self.assertIn("missing", launched.launch_error)

        timed = repository._run_bounded_git(
            ["git", "diff"],
            lambda *_args, **_kwargs: FakeProcess(timeout=True),
            FakeTimeout,
            10,
            10,
            1,
        )
        self.assertTrue(timed.timed_out)
        self.assertEqual(timed.returncode, -9)

        malformed = repository._run_bounded_git(
            ["git", "diff"],
            lambda *_args, **_kwargs: FakeProcess(stdout="not bytes"),
            FakeTimeout,
            10,
            10,
            1,
        )
        self.assertIn("non-bytes", malformed.read_error)

    def test_bounded_runner_scrubs_secret_and_git_helper_environment(self):
        repository = importlib.import_module("ambient_codex.repository")

        class EmptyPipe:
            def read(self, _limit):
                return b""

            def close(self):
                pass

        class CompletedProcess:
            stdout = EmptyPipe()
            stderr = EmptyPipe()
            returncode = 0

            def wait(self, timeout):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        captured = {}

        def popen(*_args, **kwargs):
            captured.update(kwargs)
            return CompletedProcess()

        environment = {
            "AMBIENT_CODEX_API_KEY": "secret",
            "AMBIENT_API_KEY": "shared-secret",
            "GITHUB_TOKEN": "token",
            "GIT_EXTERNAL_DIFF": "/tmp/hostile",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "diff.external",
            "GIT_CONFIG_VALUE_0": "/tmp/hostile",
            "git_external_diff": "/tmp/lowercase-hostile",
            "Git_Config_Count": "2",
            "git_config_parameters": "'diff.external'='/tmp/hostile'",
            "SAFE_VALUE": "preserved",
        }
        with mock.patch.dict(repository.os.environ, environment, clear=False):
            result = repository._run_bounded_git(
                ["git", "diff"],
                popen,
                subprocess.TimeoutExpired,
                100,
                100,
                1,
            )

        self.assertEqual(result.returncode, 0)
        child_env = captured["env"]
        for name in environment:
            if name != "SAFE_VALUE":
                self.assertNotIn(name, child_env)
        self.assertEqual(child_env["SAFE_VALUE"], "preserved")
        self.assertEqual(child_env["GIT_PAGER"], "")
        self.assertEqual(child_env["PAGER"], "")
        self.assertEqual(child_env["GIT_ATTR_NOSYSTEM"], "1")

    def test_unsafe_changed_paths_are_reported_as_omitted(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as outside_td:
            root = os.path.realpath(td)
            outside = Path(outside_td) / "outside.py"
            outside.write_bytes(b"outside\n")
            link = Path(root) / "link.py"
            try:
                link.symlink_to(outside)
            except OSError as error:
                self.skipTest("symlink creation unavailable: {0}".format(error))
            responses = (
                command_result(stdout=b"true\n"),
                command_result(stdout=b"patch\n"),
                command_result(stdout=os.fsencode(root) + b"\n"),
                command_result(
                    stdout=b"link.py\0../escape.py\0/absolute.py\0deleted.py\0"
                ),
            )
            with mock.patch.object(
                    repository,
                    "_run_bounded_git",
                    side_effect=responses,
                    create=True,
            ):
                snapshot, failure = repository.capture_git_diff(
                    True, None, object(), subprocess.TimeoutExpired, 10_000,
                )

        self.assertIsNone(failure)
        self.assertEqual(
            snapshot.changed_files,
            (("deleted.py", os.path.join(root, "deleted.py")),),
        )
        self.assertEqual(
            snapshot.omitted_paths,
            ("link.py", "../escape.py", "/absolute.py"),
        )


@unittest.skipUnless(shutil.which("git"), "Git executable unavailable")
class RealGitDiffIntegrationTests(unittest.TestCase):
    def test_staged_diff_from_subdirectory_includes_full_changed_files(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            (root / "src").mkdir()
            files = {
                "src/main.py": b"def f():\n    return 'UNIQUE-MARKER'\n",
                "src/space name.py": b"SPACE = True\n",
                "src/\u914d\u7f6e.py": "VALUE = '\u914d\u7f6e'\n".encode("utf-8"),
            }
            for relative, body in files.items():
                (root / relative).write_bytes(body)
            run_git(root, "add", "--all")

            with changed_directory(root / "src"):
                snapshot, failure = repository.capture_git_diff(
                    True,
                    None,
                    subprocess.Popen,
                    subprocess.TimeoutExpired,
                    200_000,
                )

        self.assertIsNone(failure)
        self.assertIn("UNIQUE-MARKER", snapshot.diff_text)
        self.assertEqual(snapshot.root, os.path.realpath(str(root)))
        self.assertEqual(
            {label for label, _full in snapshot.changed_files},
            set(files),
        )
        self.assertEqual(snapshot.omitted_paths, ())

    @unittest.skipIf(os.name == "nt", "newline filenames are POSIX-only")
    def test_nul_path_capture_preserves_embedded_newline(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            name = "line\nbreak.py"
            (root / name).write_bytes(b"VALUE = 1\n")
            run_git(root, "add", "--all")

            with changed_directory(root):
                snapshot, failure = repository.capture_git_diff(
                    True,
                    None,
                    subprocess.Popen,
                    subprocess.TimeoutExpired,
                    100_000,
                )

        self.assertIsNone(failure)
        self.assertEqual(snapshot.changed_files[0][0], name)

    def test_oversized_diff_terminates_and_fails_without_partial_snapshot(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            (root / "large.py").write_bytes(b"x = 1\n" * 10_000)
            run_git(root, "add", "--all")
            started = time.monotonic()

            with changed_directory(root):
                snapshot, failure = repository.capture_git_diff(
                    True,
                    None,
                    subprocess.Popen,
                    subprocess.TimeoutExpired,
                    512,
                )

        self.assertIsNone(snapshot)
        self.assertIn("exceeds", failure.message)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_revision_diff_against_head_uses_worktree_changes(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            run_git(root, "config", "user.email", "test@example.invalid")
            run_git(root, "config", "user.name", "Ambient Test")
            path = root / "source.py"
            path.write_bytes(b"VALUE = 1\n")
            run_git(root, "add", "source.py")
            run_git(root, "commit", "-q", "-m", "baseline")
            path.write_bytes(b"VALUE = 2\n")

            with changed_directory(root):
                snapshot, failure = repository.capture_git_diff(
                    False,
                    "HEAD",
                    subprocess.Popen,
                    subprocess.TimeoutExpired,
                    100_000,
                )

        self.assertIsNone(failure)
        self.assertIn("VALUE = 2", snapshot.diff_text)
        self.assertEqual(snapshot.changed_files[0][0], "source.py")

    @unittest.skipIf(os.name == "nt", "shell textconv fixture is POSIX-only")
    def test_repo_textconv_command_is_never_executed(self):
        repository = importlib.import_module("ambient_codex.repository")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_git(root, "init", "-q")
            run_git(root, "config", "user.email", "test@example.invalid")
            run_git(root, "config", "user.name", "Ambient Test")
            marker = root / "TEXTCONV-EXECUTED"
            helper = root / "textconv.sh"
            helper.write_text(
                "#!/bin/sh\n: > \"{0}\"\nexit 0\n".format(marker),
                encoding="utf-8",
            )
            helper.chmod(0o755)
            (root / ".gitattributes").write_bytes(b"*.py diff=hostile\n")
            source = root / "source.py"
            source.write_bytes(b"VALUE = 1\n")
            run_git(root, "config", "diff.hostile.textconv", str(helper))
            run_git(root, "config", "diff.external", str(helper))
            run_git(root, "add", ".gitattributes", "source.py")
            run_git(root, "commit", "-q", "-m", "baseline")
            source.write_bytes(b"VALUE = 2\n")

            with changed_directory(root):
                snapshot, failure = repository.capture_git_diff(
                    False,
                    "HEAD",
                    subprocess.Popen,
                    subprocess.TimeoutExpired,
                    100_000,
                )

        self.assertIsNone(failure)
        self.assertIsNotNone(snapshot)
        self.assertFalse(marker.exists(), "repository textconv command executed")


class GitDiffImportAndFacadeTests(unittest.TestCase):
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

    def test_facade_composes_capture_aggregate_intake_and_gutters(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            snapshot = facade._repository_core.GitDiffSnapshot(
                "patch\n",
                "/repo",
                (("a.py", "/repo/a.py"),),
                ("outside.py",),
            )
            stderr = io.StringIO()
            with mock.patch.object(
                    facade._repository_core,
                    "capture_git_diff",
                    return_value=(snapshot, None),
            ) as capture, mock.patch.object(
                    facade._intake_core,
                    "read_files",
                    return_value=((('/repo/a.py', 'x\n'),), ("notice\x1b[31m",), None),
            ) as read, contextlib.redirect_stderr(stderr):
                labeled = facade.git_diff_inputs(True, None)

        self.assertEqual(labeled, [
            ("DIFF (git)", "patch\n"),
            ("a.py", " 1| x\n 2| "),
        ])
        capture.assert_called_once_with(
            True,
            None,
            facade.subprocess.Popen,
            facade.subprocess.TimeoutExpired,
            facade.ABS_MAX_CHARS,
        )
        read.assert_called_once_with(
            ("/repo/a.py",),
            facade.ABS_MAX_CHARS - len("patch\n"),
        )
        self.assertIn("notice", stderr.getvalue())
        self.assertNotIn("\x1b", stderr.getvalue())
        self.assertIn("outside.py", stderr.getvalue())

    def test_facade_maps_failure_to_historical_usage_exit(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            failure = facade._repository_core.GitDiffFailure(
                "usage",
                "--staged/--diff must run inside a git repository.",
                True,
            )
            with mock.patch.object(
                    facade._repository_core,
                    "capture_git_diff",
                    return_value=(None, failure),
            ), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(
                    SystemExit
            ) as raised:
                facade.git_diff_inputs(True, None)

        self.assertEqual(raised.exception.code, facade.EXIT_USAGE)

    def test_facade_rejects_intake_and_post_gutter_aggregate_overflow(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            snapshot = facade._repository_core.GitDiffSnapshot(
                "12345", "/repo", (("a.py", "/repo/a.py"),), (),
            )
            with mock.patch.object(facade, "ABS_MAX_CHARS", 10), \
                    mock.patch.object(
                        facade._repository_core,
                        "capture_git_diff",
                        return_value=(snapshot, None),
                    ), mock.patch.object(
                        facade._intake_core,
                        "read_files",
                        return_value=((), (), "/repo/a.py"),
                    ), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                facade.git_diff_inputs(True, None)

            with mock.patch.object(facade, "ABS_MAX_CHARS", 10), \
                    mock.patch.object(
                        facade._repository_core,
                        "capture_git_diff",
                        return_value=(snapshot, None),
                    ), mock.patch.object(
                        facade._intake_core,
                        "read_files",
                        return_value=((('/repo/a.py', 'abcde'),), (), None),
                    ), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                facade.git_diff_inputs(True, None)


if __name__ == "__main__":
    unittest.main()
