"""Phase 2B2 contracts for defensive config parsing and reads."""

import importlib
import importlib.machinery
import importlib.util
import inspect
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
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
        loader = importlib.machinery.SourceFileLoader("ambient_phase2b2", str(BIN))
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


class InternalConfigReadTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        config_store = importlib.import_module("ambient_codex.config_store")

        self.assertEqual(config_store.__all__, MOVED_NAMES)

    def test_parser_preserves_existing_env_line_semantics(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        lines = (
            "  # comment  ",
            "bare line",
            " A = first ",
            "B=x=y",
            "A=last",
            "=empty-key",
            "   ",
        )

        self.assertEqual(
            config_store.parse_config_lines(lines),
            {"A": "last", "B": "x=y", "": "empty-key"},
        )

    def test_missing_file_is_silent(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        err = io.StringIO()

        self.assertEqual(
            config_store.read_config_file(
                "/definitely/missing/ambient/env", "ambient-codex", err, os.name
            ),
            {},
        )
        self.assertEqual(err.getvalue(), "")

        fake_stat = types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=os.getuid() if hasattr(os, "getuid") else 0,
        )
        with mock.patch.object(config_store.os, "lstat", return_value=fake_stat), \
             mock.patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(
                config_store.read_config_file(
                    "/vanished/ambient/env", "ambient-codex", err, os.name
                ),
                {},
            )
        self.assertEqual(err.getvalue(), "")

    @unittest.skipUnless(os.name == "posix", "POSIX owner and mode semantics")
    def test_posix_regular_owner_file_is_healed_then_parsed(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text(" A = 1\nA=2\n", encoding="utf-8")
            os.chmod(path, 0o644)
            err = io.StringIO()

            conf = config_store.read_config_file(
                str(path), "ambient-codex", err, "posix"
            )

            self.assertEqual(conf, {"A": "2"})
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertIn(f"tightened {path} permissions (644 -> 600)", err.getvalue())

    def test_windows_mode_does_not_chmod_or_report(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_text("A=1\n", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(config_store.os, "chmod") as chmod:
                conf = config_store.read_config_file(
                    str(path), "ambient-codex", err, "nt"
                )

            self.assertEqual(conf, {"A": "1"})
            chmod.assert_not_called()
            self.assertNotIn("tightened", err.getvalue())

    @unittest.skipIf(os.name == "nt", "symlink setup differs on Windows")
    def test_symlink_is_ignored_without_chmodding_its_target(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target"
            target.write_text("A=secret\n", encoding="utf-8")
            os.chmod(target, 0o644)
            link = Path(td) / "env"
            link.symlink_to(target)
            err = io.StringIO()

            conf = config_store.read_config_file(
                str(link), "ambient-codex", err, "posix"
            )

            self.assertEqual(conf, {})
            self.assertIn("is not a regular file", err.getvalue())
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o644)

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX ownership semantics")
    def test_foreign_owner_is_ignored_before_open(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        fake_stat = types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=os.getuid() + 1,
        )
        err = io.StringIO()
        with mock.patch.object(config_store.os, "lstat", return_value=fake_stat), \
             mock.patch("builtins.open") as open_file:
            conf = config_store.read_config_file(
                "/tmp/foreign-env", "ambient-codex", err, "posix"
            )

        self.assertEqual(conf, {})
        self.assertIn("is not owned by you", err.getvalue())
        open_file.assert_not_called()

    def test_invalid_utf8_and_read_errors_degrade_with_guidance(self):
        config_store = importlib.import_module("ambient_codex.config_store")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "env"
            path.write_bytes(b"\xff\xfe\x00bad")
            err = io.StringIO()
            self.assertEqual(
                config_store.read_config_file(
                    str(path), "ambient-codex", err, os.name
                ),
                {},
            )
            self.assertIn("corrupt (not valid UTF-8)", err.getvalue())
            self.assertIn("ambient-codex setup", err.getvalue())

            path.write_text("A=1\n", encoding="utf-8")
            err = io.StringIO()
            with mock.patch("builtins.open", side_effect=PermissionError("denied")):
                self.assertEqual(
                    config_store.read_config_file(
                        str(path), "ambient-codex", err, os.name
                    ),
                    {},
                )
            self.assertIn(f"cannot read {path}: denied", err.getvalue())

            err = io.StringIO()
            with mock.patch.object(
                config_store.os, "lstat", side_effect=PermissionError("blocked")
            ):
                self.assertEqual(
                    config_store.read_config_file(
                        str(path), "ambient-codex", err, os.name
                    ),
                    {},
                )
            self.assertIn(f"cannot read {path}: blocked", err.getvalue())

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


class FacadeConfigReadTests(unittest.TestCase):
    def test_facade_keeps_zero_arg_patchable_path_and_platform(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            facade = load_facade(base / "home")
            path = base / "env"
            path.write_text("A=1\n", encoding="utf-8")
            os.chmod(path, 0o644)
            err = io.StringIO()

            with mock.patch.object(facade, "CONFIG_PATH", str(path)), \
                 mock.patch.object(facade.os, "name", "nt"), \
                 mock.patch.object(facade.sys, "stderr", err):
                conf = facade.read_config_file()

            self.assertEqual(list(inspect.signature(facade.read_config_file).parameters), [])
            self.assertEqual(conf, {"A": "1"})
            self.assertNotIn("tightened", err.getvalue())


if __name__ == "__main__":
    unittest.main()
