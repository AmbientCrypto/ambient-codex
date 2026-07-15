"""Phase 1A contracts for the immutable constants boundary."""

import ast
import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
CONSTANTS = ROOT / "ambient_codex" / "constants.py"
CI_WORKFLOW = ROOT.parent.parent / ".github" / "workflows" / "ci.yml"

MOVED_NAMES = (
    "AMBIENT_CODEX_HOME_ENV",
    "STATE_MARKER",
    "KEYCHAIN_SERVICE",
    "KEYCHAIN_ACCOUNT",
    "API_KEY_ENV",
    "SHARED_API_KEY_ENV",
    "LAUNCHER_NAME",
    "DEFAULT_API_URL",
    "KEY_CONSOLE_URL",
    "SUPPORT_LINE",
    "EXIT_PARTIAL",
    "EXIT_UNCONFIGURED",
    "EXIT_USAGE",
    "ANSI_RE",
    "CTRL_RE",
    "DEFAULT_MODEL",
    "DEFAULT_CODE_MODEL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_MAX_TOKENS",
    "MAX_REQUESTED_TOKENS",
    "MIN_OUTPUT_TOKENS",
    "HEARTBEAT_S",
    "MAX_AUTO_BUDGET_TOKENS",
    "STREAM_LINE_MAX",
    "CHUNK_CHARS",
    "CHARS_PER_TOKEN",
    "INPUT_TOKEN_SAFETY",
    "REASONING_EXPANSION",
    "TELEMETRY_CPT_MIN",
    "TELEMETRY_CPT_MAX",
    "TELEMETRY_EWMA_ALPHA",
    "ANSWER_TOKENS_RESERVE",
    "OUTPUT_SAFETY",
    "CONTEXT_OVERHEAD_TOKENS",
    "REASONING_SINGLE_SHOT_CHARS",
    "SINGLE_SHOT_MAX_CHARS_DEFAULT",
    "REASONING_CHUNK_FACTOR",
    "MIN_REASONING_CHUNK",
    "NONREASONING_OUTPUT_BUDGET",
    "NONREASONING_CONTEXT_MARGIN",
    "FALLBACK_CONTEXT",
    "FALLBACK_MAX_OUTPUT",
    "ABS_MAX_CHARS",
    "MAX_PARALLEL_CHUNKS",
    "CODE_MAP_BUDGET_DEFAULT",
    "CODE_MAP_BUDGET_MAX",
    "CODE_MAP_SIGS_PER_FILE",
    "SIG_SCAN_LINE_MAX",
    "REPO_FILE_MAX_BYTES",
    "REPO_SKIP_DIRS",
    "REPO_LOCKFILES",
)


def load_facade():
    loader = importlib.machinery.SourceFileLoader("ambient_phase1_facade", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def assigned_module_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


class InternalConstantsTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        constants = importlib.import_module("ambient_codex.constants")

        self.assertEqual(constants.__all__, MOVED_NAMES)
        assigned = assigned_module_names(CONSTANTS) - {"__all__"}
        self.assertEqual(assigned, frozenset(MOVED_NAMES))

    def test_representative_values_and_types_are_frozen(self):
        constants = importlib.import_module("ambient_codex.constants")

        self.assertEqual(constants.DEFAULT_API_URL, "https://api.ambient.xyz")
        self.assertEqual(constants.DEFAULT_MODEL, "moonshotai/kimi-k2.7-code")
        self.assertEqual(constants.MAX_REQUESTED_TOKENS, 1_000_000)
        self.assertEqual(constants.ABS_MAX_CHARS, 20_000_000)
        self.assertEqual(constants.REPO_FILE_MAX_BYTES, constants.ABS_MAX_CHARS)
        self.assertIsInstance(constants.REPO_SKIP_DIRS, frozenset)
        self.assertIsInstance(constants.REPO_LOCKFILES, frozenset)
        self.assertIn(".git", constants.REPO_SKIP_DIRS)
        self.assertIn("Package.resolved", constants.REPO_LOCKFILES)
        self.assertIsInstance(constants.ANSI_RE, re.Pattern)
        self.assertIsInstance(constants.CTRL_RE, re.Pattern)
        self.assertIn("\\x1b", constants.ANSI_RE.pattern)
        self.assertIn("\\x7f", constants.CTRL_RE.pattern)

    def test_internal_import_has_no_state_or_environment_side_effects(self):
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
                [sys.executable, "-c", "import ambient_codex.constants"],
                cwd=str(home),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeCompatibilityTests(unittest.TestCase):
    def test_facade_reexports_every_internal_constant(self):
        constants = importlib.import_module("ambient_codex.constants")
        facade = load_facade()

        for name in MOVED_NAMES:
            self.assertEqual(getattr(facade, name), getattr(constants, name), name)

    def test_facade_keeps_patchable_bindings_without_duplicate_assignments(self):
        constants = importlib.import_module("ambient_codex.constants")
        facade = load_facade()

        self.assertTrue(frozenset(MOVED_NAMES).isdisjoint(assigned_module_names(BIN)))
        facade.DEFAULT_MODEL = "test/model"
        self.assertEqual(facade.DEFAULT_MODEL, "test/model")
        self.assertEqual(constants.DEFAULT_MODEL, "moonshotai/kimi-k2.7-code")


class TestSuiteIsolationTests(unittest.TestCase):
    def test_ci_discovery_imports_the_tests_package_hermeticity_guard(self):
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        guarded = "unittest discover -s tests -t . -q"
        self.assertGreaterEqual(workflow.count(guarded), 2)
        self.assertNotIn("unittest discover -s tests -q", workflow)


if __name__ == "__main__":
    unittest.main()
