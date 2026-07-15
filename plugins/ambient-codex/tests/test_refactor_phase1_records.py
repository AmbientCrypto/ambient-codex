"""Phase 1B contracts for dependency-free record and error types."""

import ast
import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
RECORDS = ROOT / "ambient_codex" / "records.py"
MOVED_NAMES = ("ModelProfile", "NetworkError", "ChatError", "StallError")
PROFILE_FIELDS = (
    "model",
    "is_reasoning",
    "context_length",
    "max_output_length",
    "output_budget",
    "single_shot_chars",
    "chunk_chars",
    "escalation_ceiling",
    "features",
)


def load_facade():
    loader = importlib.machinery.SourceFileLoader("ambient_phase1b_facade", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def owned_module_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return frozenset(names) - {"__all__"}


class InternalRecordsTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        records = importlib.import_module("ambient_codex.records")

        self.assertEqual(records.__all__, MOVED_NAMES)
        self.assertEqual(owned_module_names(RECORDS), frozenset(MOVED_NAMES))

    def test_model_profile_preserves_namedtuple_contract(self):
        records = importlib.import_module("ambient_codex.records")

        self.assertEqual(records.ModelProfile._fields, PROFILE_FIELDS)
        profile = records.ModelProfile(
            "model/x", True, 200_000, 65_536, 32_000,
            100_000, 85_000, 65_536, ["reasoning"],
        )
        self.assertIsInstance(profile, tuple)
        self.assertEqual(profile.model, "model/x")
        self.assertEqual(profile.features, ["reasoning"])
        self.assertEqual(profile._replace(output_budget=4_096).output_budget, 4_096)

    def test_error_payloads_and_defaults_are_stable(self):
        records = importlib.import_module("ambient_codex.records")

        network = records.NetworkError("offline")
        self.assertIsInstance(network, Exception)
        self.assertEqual(str(network), "offline")

        chat = records.ChatError("auth", "key rejected")
        self.assertEqual(str(chat), "key rejected")
        self.assertEqual(chat.category, "auth")
        self.assertEqual(chat.diagnosis, "key rejected")

        stall = records.StallError("stalled")
        self.assertEqual(str(stall), "stalled")
        self.assertEqual(stall.partial, "")
        self.assertEqual(stall.reasoning, "")
        self.assertFalse(stall.hard_wall)
        rich = records.StallError("wall", "partial", "thinking", True)
        self.assertEqual(
            (rich.partial, rich.reasoning, rich.hard_wall),
            ("partial", "thinking", True),
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
                [sys.executable, "-c", "import ambient_codex.records"],
                cwd=str(home),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeRecordsCompatibilityTests(unittest.TestCase):
    def test_facade_reexports_the_same_type_objects(self):
        records = importlib.import_module("ambient_codex.records")
        facade = load_facade()

        for name in MOVED_NAMES:
            self.assertIs(getattr(facade, name), getattr(records, name), name)
        self.assertTrue(frozenset(MOVED_NAMES).isdisjoint(owned_module_names(BIN)))

    def test_facade_type_bindings_remain_patchable(self):
        records = importlib.import_module("ambient_codex.records")
        facade = load_facade()

        replacement = type("ReplacementChatError", (Exception,), {})
        facade.ChatError = replacement
        self.assertIs(facade.ChatError, replacement)
        self.assertIsNot(records.ChatError, replacement)


if __name__ == "__main__":
    unittest.main()
