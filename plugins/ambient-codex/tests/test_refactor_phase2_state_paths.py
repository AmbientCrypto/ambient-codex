"""Phase 2A contracts for path and state-root validation."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = ("resolve", "is_within", "foreign_root", "state_root_error")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2a", str(BIN))
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


class InternalStatePathTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        state = importlib.import_module("ambient_codex.state")

        self.assertEqual(state.__all__, MOVED_NAMES)

    def test_resolve_and_containment_are_prefix_and_parent_safe(self):
        state = importlib.import_module("ambient_codex.state")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            child = root / "nested" / ".." / "file"
            sibling_prefix = Path(f"{root}-foreign") / "file"

            expected = os.path.normcase(os.path.realpath(root / "file"))
            self.assertEqual(state.resolve(child), expected)
            self.assertTrue(state.is_within(child, root))
            self.assertTrue(state.is_within(root, root))
            self.assertFalse(state.is_within(sibling_prefix, root))

    def test_cross_drive_commonpath_error_is_not_containment(self):
        state = importlib.import_module("ambient_codex.state")

        with mock.patch.object(state.os.path, "commonpath", side_effect=ValueError):
            self.assertFalse(state.is_within("child", "parent"))

    def test_foreign_root_uses_only_the_explicit_root_sequence(self):
        state = importlib.import_module("ambient_codex.state")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            foreign = base / "foreign"
            unrelated = base / "unrelated"

            self.assertEqual(
                state.foreign_root(foreign / "cache", (str(foreign),)),
                str(foreign),
            )
            self.assertIsNone(state.foreign_root(unrelated, (str(foreign),)))

    def test_state_root_errors_preserve_exact_categories_and_guidance(self):
        state = importlib.import_module("ambient_codex.state")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            foreign = base / "foreign"
            foreign.mkdir()
            blocked = foreign / "cache"
            error = state.state_root_error(
                str(blocked), (str(foreign),), ".ambient-codex",
                "AMBIENT_CODEX_HOME",
            )
            self.assertIn(f"refusing to use {blocked}", error)
            self.assertIn(f"inside {foreign}", error)
            self.assertIn("belongs to another Ambient install", error)
            self.assertIn("Unset AMBIENT_CODEX_HOME", error)

            unclaimed = base / "unclaimed"
            unclaimed.mkdir()
            (unclaimed / "env").write_text("x=1\n", encoding="utf-8")
            error = state.state_root_error(
                str(unclaimed), (), ".ambient-codex", "AMBIENT_CODEX_HOME"
            )
            self.assertIn("did not create", error)
            self.assertIn("no .ambient-codex marker", error)

    def test_fresh_or_claimed_roots_are_accepted(self):
        state = importlib.import_module("ambient_codex.state")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            fresh = base / "fresh"
            self.assertIsNone(
                state.state_root_error(
                    str(fresh), (), ".ambient-codex", "AMBIENT_CODEX_HOME"
                )
            )
            fresh.mkdir()
            (fresh / "env").write_text("x=1\n", encoding="utf-8")
            (fresh / ".ambient-codex").write_text("owned\n", encoding="utf-8")
            self.assertIsNone(
                state.state_root_error(
                    str(fresh), (), ".ambient-codex", "AMBIENT_CODEX_HOME"
                )
            )

    def test_internal_import_has_no_external_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "AMBIENT_CODEX_HOME": str(home / "state"),
                "HOME": str(home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.state"],
                cwd=str(home),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeStatePathTests(unittest.TestCase):
    def test_facade_uses_patchable_foreign_roots_and_preserves_exit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            facade = load_facade(base / "home")
            foreign = base / "custom-foreign"
            facade.FOREIGN_STATE_DIRS = (str(foreign),)

            self.assertEqual(facade.foreign_root(foreign / "cache"), str(foreign))
            with self.assertRaises(SystemExit) as raised:
                facade.validate_state_root(str(foreign / "cache"))
            self.assertIn("belongs to another Ambient install", str(raised.exception))

    def test_facade_path_helpers_match_internal_results(self):
        state = importlib.import_module("ambient_codex.state")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            facade = load_facade(base / "home")
            child = base / "root" / "nested"

            self.assertEqual(facade._resolve(child), state.resolve(child))
            self.assertEqual(
                facade._is_within(child, base / "root"),
                state.is_within(child, base / "root"),
            )


if __name__ == "__main__":
    unittest.main()
