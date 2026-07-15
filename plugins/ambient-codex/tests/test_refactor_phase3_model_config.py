"""Phase 3C contracts for model selection and untrusted catalog fields."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = ("model_map", "resolve_model", "as_pos_int", "as_bool",
               "ready_model_ids")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase3c", str(BIN))
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


class ModelConfigOwnershipTests(unittest.TestCase):
    def test_module_owns_exact_exports(self):
        model_config = importlib.import_module("ambient_codex.model_config")
        self.assertEqual(model_config.__all__, MOVED_NAMES)


class ModelConfigBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.model_config")

    def test_model_map_accepts_only_nonempty_normalized_assignments(self):
        environ = {"AMBIENT_MODEL_MAP": " map = one,broken,=two,CODE= three "}
        self.assertEqual(self.core.model_map({}, environ),
                         {"map": "one", "code": "three"})

    def test_explicit_model_beats_phase_map_and_defaults(self):
        args = type("Args", (), {"model": "explicit/model"})()
        result = self.core.resolve_model(
            args, {"AMBIENT_MODEL": "saved/model", "AMBIENT_MODEL_MAP": "map=m"},
            "chat", "map", {"AMBIENT_MODEL": "env/model"}, "fallback/chat",
            "fallback/code")
        self.assertEqual(result, "explicit/model")

    def test_catalog_field_coercers_reject_bool_and_false_strings(self):
        self.assertEqual(self.core.as_pos_int(True, 9), 9)
        self.assertEqual(self.core.as_pos_int("12", 9), 12)
        self.assertTrue(self.core.as_bool("yes"))
        self.assertFalse(self.core.as_bool("false"))
        self.assertEqual(
            self.core.ready_model_ids([
                {"id": "ready", "is_ready": "true"},
                {"id": "cold", "is_ready": "0"}, {"id": "", "is_ready": True},
            ]), ["ready"])


class ModelConfigFacadeTests(unittest.TestCase):
    def test_facade_model_map_delegates_live_environment(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(facade._model_config, "model_map",
                                   return_value={"chat": "m"}) as call:
                self.assertEqual(facade.model_map({}), {"chat": "m"})
            call.assert_called_once_with({}, facade.os.environ)

    def test_facade_resolve_model_injects_compatibility_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            args = type("Args", (), {"model": None})()
            with mock.patch.object(facade._model_config, "resolve_model",
                                   return_value="chosen") as call:
                self.assertEqual(facade.resolve_model(args, {}, "code"), "chosen")
            call.assert_called_once_with(
                args, {}, "code", None, facade.os.environ,
                facade.DEFAULT_MODEL, facade.DEFAULT_CODE_MODEL,
            )
