"""Phase 2C2B contracts for liveness-safe stdin intake."""

import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import math
import os
from pathlib import Path
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


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c2b", str(BIN))
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


class StdinWaitTests(unittest.TestCase):
    def test_wait_override_is_finite_clamped_and_does_not_mutate_environment(self):
        intake = importlib.import_module("ambient_codex.intake")
        cases = (
            ({}, 10.0),
            ({"AMBIENT_STDIN_WAIT": "2.5"}, 2.5),
            ({"AMBIENT_STDIN_WAIT": "0"}, 0.1),
            ({"AMBIENT_STDIN_WAIT": "999999"}, 60.0),
            ({"AMBIENT_STDIN_WAIT": "not-a-number"}, 10.0),
            ({"AMBIENT_STDIN_WAIT": "nan"}, 10.0),
            ({"AMBIENT_STDIN_WAIT": "inf"}, 10.0),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment):
                before = dict(environment)
                actual = intake.stdin_wait_seconds(environment, 10, 60)
                self.assertEqual(actual, expected)
                self.assertEqual(environment, before)
                self.assertTrue(math.isfinite(actual))

    def test_invalid_default_or_maximum_wait_fails_fast(self):
        intake = importlib.import_module("ambient_codex.intake")
        for default, maximum in (
            (0, 60), (10, 0), (math.inf, 60), (10, True), (70, 60)
        ):
            with self.subTest(default=default, maximum=maximum), \
                    self.assertRaises(ValueError):
                intake.stdin_wait_seconds({}, default, maximum)


class StdinDecodeTests(unittest.TestCase):
    def test_binary_stream_is_lossy_bounded_and_warns_for_any_nul(self):
        intake = importlib.import_module("ambient_codex.intake")
        payload = b"caf\xe9" + b"a" * 9_000 + b"\x00tail"
        stream = types.SimpleNamespace(buffer=io.BytesIO(payload))

        text, warnings, error = intake.read_stdin_text(stream, 20_000)

        self.assertIn("caf\ufffd", text)
        self.assertNotIn("\x00", text)
        self.assertEqual(warnings, ("stdin looks binary — decoding lossily "
                                    "(NUL bytes stripped)",))
        self.assertIsNone(error)

    def test_text_stream_preserves_content_without_warning(self):
        intake = importlib.import_module("ambient_codex.intake")

        text, warnings, error = intake.read_stdin_text(io.StringIO("a\r\nb"), 10)

        self.assertEqual(text, "a\r\nb")
        self.assertEqual(warnings, ())
        self.assertIsNone(error)

    def test_over_limit_text_returns_explicit_existing_error(self):
        intake = importlib.import_module("ambient_codex.intake")

        text, warnings, error = intake.read_stdin_text(io.StringIO("abcdef"), 5)

        self.assertIsNone(text)
        self.assertEqual(warnings, ())
        self.assertIn("stdin exceeds 5 chars", error)
        self.assertIn("split the job", error)

    def test_nul_stripping_cannot_hide_an_over_limit_byte_stream(self):
        intake = importlib.import_module("ambient_codex.intake")
        stream = types.SimpleNamespace(buffer=io.BytesIO(b"\x00" * 21))

        text, warnings, error = intake.read_stdin_text(stream, 5)

        self.assertIsNone(text)
        self.assertEqual(warnings, ("stdin looks binary — decoding lossily "
                                    "(NUL bytes stripped)",))
        self.assertIn("stdin exceeds 5 chars", error)

    def test_invalid_utf8_and_stream_read_errors_are_explicit(self):
        intake = importlib.import_module("ambient_codex.intake")

        class InvalidText:
            def read(self, _limit):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

        class BrokenText:
            def read(self, _limit):
                raise OSError("pipe failed")

        invalid = intake.read_stdin_text(InvalidText(), 10)
        broken = intake.read_stdin_text(BrokenText(), 10)

        self.assertEqual(invalid, (None, (), "stdin is not valid UTF-8 text"))
        self.assertIsNone(broken[0])
        self.assertIn("cannot read stdin: pipe failed", broken[2])

    def test_non_bytes_buffer_and_invalid_character_cap_are_rejected(self):
        intake = importlib.import_module("ambient_codex.intake")
        bad_buffer = types.SimpleNamespace(
            buffer=types.SimpleNamespace(read=lambda _limit: "not bytes")
        )

        _text, _warnings, error = intake.read_stdin_text(bad_buffer, 10)

        self.assertIn("byte stream returned non-bytes data", error)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                intake.read_stdin_text(io.StringIO(), invalid)

        non_text = types.SimpleNamespace(read=lambda _limit: b"not text")
        self.assertIn(
            "text stream returned non-text data",
            intake.read_stdin_text(non_text, 10)[2],
        )


class BoundedWorkerTests(unittest.TestCase):
    def test_fast_reader_returns_an_immutable_success_outcome(self):
        intake = importlib.import_module("ambient_codex.intake")

        outcome = intake.read_stdin_bounded(lambda: "piped", 1, threading.Thread)

        self.assertEqual(outcome, ("piped", False))
        self.assertIsInstance(outcome, tuple)

    def test_blocked_reader_times_out_without_hanging(self):
        intake = importlib.import_module("ambient_codex.intake")

        def blocked():
            time.sleep(0.5)
            return "late"

        started = time.monotonic()
        outcome = intake.read_stdin_bounded(blocked, 0.02, threading.Thread)

        self.assertEqual(outcome, (None, True))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_worker_exceptions_are_re_raised_on_the_calling_thread(self):
        intake = importlib.import_module("ambient_codex.intake")

        def broken():
            raise RuntimeError("reader exploded")

        with self.assertRaisesRegex(RuntimeError, "reader exploded"):
            intake.read_stdin_bounded(broken, 1, threading.Thread)

        with self.assertRaises(SystemExit) as raised:
            intake.read_stdin_bounded(
                lambda: (_ for _ in ()).throw(SystemExit("input failed")),
                1,
                threading.Thread,
            )
        self.assertEqual(raised.exception.code, "input failed")

    def test_invalid_timeout_fails_before_starting_a_thread(self):
        intake = importlib.import_module("ambient_codex.intake")
        thread_factory = mock.Mock()
        for invalid in (0, -1, math.nan, math.inf, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                intake.read_stdin_bounded(lambda: "x", invalid, thread_factory)
        thread_factory.assert_not_called()

        with self.assertRaises(ValueError):
            intake.read_stdin_bounded(None, 1, thread_factory)

    def test_malformed_worker_outcomes_fail_explicitly(self):
        intake = importlib.import_module("ambient_codex.intake")
        with self.assertRaisesRegex(TypeError, "must return text"):
            intake.read_stdin_bounded(lambda: None, 1, threading.Thread)

        class ThreadWithoutWorker:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

            def join(self, _wait):
                pass

            def is_alive(self):
                return False

        with self.assertRaisesRegex(RuntimeError, "ended without a result"):
            intake.read_stdin_bounded(lambda: "x", 1, ThreadWithoutWorker)


class ReadinessTests(unittest.TestCase):
    def test_selector_states_are_distinct_and_dependency_is_explicit(self):
        intake = importlib.import_module("ambient_codex.intake")
        stream = object()
        ready = mock.Mock(return_value=([stream], [], []))
        empty = mock.Mock(return_value=([], [], []))

        self.assertIs(intake.stdin_ready(stream, ready, 2), True)
        self.assertIs(intake.stdin_ready(stream, empty, 2), False)
        ready.assert_called_once_with([stream], [], [], 2)

        for error in (OSError("unsupported"), ValueError("closed"), TypeError("odd")):
            with self.subTest(error=error):
                selector = mock.Mock(side_effect=error)
                self.assertIsNone(intake.stdin_ready(stream, selector, 2))

    def test_unexpected_selector_error_is_not_silenced(self):
        intake = importlib.import_module("ambient_codex.intake")
        with self.assertRaisesRegex(RuntimeError, "selector broke"):
            intake.stdin_ready(
                object(), mock.Mock(side_effect=RuntimeError("selector broke")), 1
            )

    def test_waiting_data_probe_is_conservative_and_nonblocking(self):
        intake = importlib.import_module("ambient_codex.intake")
        tty = mock.Mock()
        tty.isatty.return_value = True
        selector = mock.Mock()
        self.assertFalse(intake.stdin_has_waiting_data(tty, selector, mock.Mock()))
        selector.assert_not_called()

        stream = mock.Mock()
        stream.isatty.return_value = False
        self.assertFalse(intake.stdin_has_waiting_data(
            stream, mock.Mock(return_value=([], [], [])), mock.Mock()
        ))
        self.assertFalse(intake.stdin_has_waiting_data(
            stream, mock.Mock(return_value=([stream], [], [])), None
        ))
        broken = mock.Mock()
        broken.isatty.side_effect = OSError("closed")
        self.assertFalse(intake.stdin_has_waiting_data(
            broken, mock.Mock(), mock.Mock()
        ))

    @unittest.skipUnless(os.name == "posix", "FIONREAD probe is POSIX-only")
    def test_waiting_data_probe_requires_positive_byte_count(self):
        intake = importlib.import_module("ambient_codex.intake")
        stream = mock.Mock()
        stream.isatty.return_value = False
        stream.fileno.return_value = 7
        selector = mock.Mock(return_value=([stream], [], []))

        def set_count(_fd, _request, count):
            count[0] = 3

        fcntl_module = types.SimpleNamespace(ioctl=set_count)

        self.assertTrue(intake.stdin_has_waiting_data(
            stream, selector, fcntl_module
        ))


class FacadeStdinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.facade = load_facade(Path(self.temp.name) / "home")

    def test_ready_stdin_binds_environment_limits_selector_and_reader(self):
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        with mock.patch.object(self.facade.sys, "stdin", fake_stdin), \
                mock.patch.object(
                    self.facade._intake_core,
                    "stdin_wait_seconds",
                    return_value=2.0,
                ) as wait, mock.patch.object(
                    self.facade._intake_core, "stdin_ready", return_value=True
                ) as ready, mock.patch.object(
                    self.facade, "_stdin_read_and_decode", return_value="body"
                ) as reader:
            result = self.facade.read_stdin_if_piped()

        self.assertEqual(result, "body")
        wait.assert_called_once_with(
            self.facade.os.environ,
            float(self.facade.STDIN_WAIT_S),
            float(self.facade.STDIN_WAIT_MAX_S),
        )
        ready.assert_called_once_with(fake_stdin, self.facade.select.select, 2.0)
        reader.assert_called_once_with()

    def test_timed_out_and_unsupported_selectors_use_bounded_paths(self):
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        err = io.StringIO()
        with mock.patch.object(self.facade.sys, "stdin", fake_stdin), \
                mock.patch.object(
                    self.facade._intake_core, "stdin_wait_seconds", return_value=3.0
                ), mock.patch.object(
                    self.facade._intake_core, "stdin_ready", return_value=False
                ), contextlib.redirect_stderr(err):
            self.assertEqual(self.facade.read_stdin_if_piped(), "")
        self.assertIn("stdin produced no data within 3s", err.getvalue())

        with mock.patch.object(self.facade.sys, "stdin", fake_stdin), \
                mock.patch.object(
                    self.facade._intake_core, "stdin_wait_seconds", return_value=3.0
                ), mock.patch.object(
                    self.facade._intake_core, "stdin_ready", return_value=None
                ), mock.patch.object(
                    self.facade, "_read_stdin_bounded", return_value="bounded"
                ) as bounded:
            self.assertEqual(self.facade.read_stdin_if_piped(), "bounded")
        self.assertEqual(bounded.call_args.args[0], 3.0)

    def test_decode_wrapper_maps_warnings_and_errors_to_existing_facade_contract(self):
        result = (None, ("stdin warning",), "stdin failed")
        err = io.StringIO()
        with mock.patch.object(
                self.facade._intake_core, "read_stdin_text", return_value=result
        ) as reader, mock.patch.object(
                self.facade, "_argv_command", return_value="audit"
        ), mock.patch.object(self.facade, "_fail_exit") as fail, \
                contextlib.redirect_stderr(err):
            self.facade._stdin_read_and_decode()

        reader.assert_called_once_with(self.facade.sys.stdin, self.facade.ABS_MAX_CHARS)
        self.assertEqual(err.getvalue(), "ambient: stdin warning\n")
        args = fail.call_args.args
        self.assertEqual(args[1:3], ("audit", "input"))
        self.assertEqual(args[3], "stdin failed")

    def test_bounded_and_ignored_wrappers_retain_patch_points_and_messages(self):
        with mock.patch.object(
                self.facade._intake_core,
                "read_stdin_bounded",
                return_value=(None, True),
        ) as bounded, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.facade._read_stdin_bounded(0.2, "timed out"), "")
        bounded.assert_called_once_with(
            self.facade._stdin_read_and_decode, 0.2, self.facade.threading.Thread
        )
        self.assertEqual(err.getvalue(), "timed out\n")

        with mock.patch.object(
                self.facade._intake_core,
                "stdin_has_waiting_data",
                return_value=True,
        ) as waiting, contextlib.redirect_stderr(io.StringIO()) as err:
            self.facade.warn_if_stdin_ignored("include it")
        waiting.assert_called_once_with(
            self.facade.sys.stdin, self.facade.select.select, self.facade.fcntl
        )
        self.assertIn("data is waiting on stdin", err.getvalue())
        self.assertIn("include it", err.getvalue())


class StdinImportTests(unittest.TestCase):
    def test_extended_intake_import_has_no_external_side_effects(self):
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


if __name__ == "__main__":
    unittest.main()
