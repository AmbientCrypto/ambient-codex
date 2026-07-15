"""Phase 3A contracts for HTTP transport and catalog normalization."""

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
MOVED_NAMES = ("api_request", "catalog_data")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase3a", str(BIN))
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


class TransportOwnershipTests(unittest.TestCase):
    def test_module_owns_exact_exports(self):
        transport = importlib.import_module("ambient_codex.transport")
        self.assertEqual(transport.__all__, MOVED_NAMES)

    def test_import_is_side_effect_free_in_fresh_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({"HOME": str(home), "USERPROFILE": str(home),
                        "PYTHONPATH": str(ROOT)})
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.transport"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class CatalogDataTests(unittest.TestCase):
    def test_only_object_rows_with_nonempty_string_ids_survive(self):
        transport = importlib.import_module("ambient_codex.transport")
        body = {"data": [
            {"id": "ready/model"}, {"id": ""}, {"id": 2}, "bad", {},
        ]}
        self.assertEqual(transport.catalog_data(body), [{"id": "ready/model"}])

    def test_degraded_bodies_normalize_to_empty_list(self):
        transport = importlib.import_module("ambient_codex.transport")
        for body in (None, [], "bad", {"data": None}, {"data": {}},
                     {"data": "not-a-list"}):
            with self.subTest(body=body):
                self.assertEqual(transport.catalog_data(body), [])


class TransportFacadeTests(unittest.TestCase):
    def test_api_request_wrapper_injects_facade_retry_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._transport, "api_request", return_value=(200, {})) as call:
                self.assertEqual(facade.api_request("https://x", "key", "/v1/models"),
                                 (200, {}))
            args, kwargs = call.call_args
            self.assertEqual(args, ("https://x", "key", "/v1/models", None,
                                    facade.DEFAULT_TIMEOUT_S))
            self.assertIs(kwargs["retry_delay"], facade._retry_delay)
            self.assertIs(kwargs["sleep"], facade.time.sleep)
            self.assertIs(kwargs["stderr"], facade.sys.stderr)

    def test_fetch_models_uses_transport_catalog_normalizer_once(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            body = {"data": [{"id": "ignored"}]}
            expected = [{"id": "kept"}]
            with mock.patch.object(facade, "api_request", return_value=(200, body)), \
                 mock.patch.object(facade._transport, "catalog_data",
                                   return_value=expected) as normalize:
                self.assertEqual(facade.fetch_models("https://x", "key"), expected)
            normalize.assert_called_once_with(body)
