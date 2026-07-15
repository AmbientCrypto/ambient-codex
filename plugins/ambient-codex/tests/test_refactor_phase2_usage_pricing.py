"""Phase 2D3-a contracts for pure pricing primitives."""

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
MOVED_NAMES = ("model_pricing", "parse_reference_price", "usage_cost",
               "reference_cost", "relative_savings_note",
               "relative_savings_note_by_served")


def _pos_int(value, default):
    """Mirror of the facade's ``_as_pos_int`` token coercer, injected into the
    pure cost-math functions under test (they take the coercer as a dep)."""
    try:
        if isinstance(value, bool):
            return default
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2d3a", str(BIN))
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


class PricingOwnershipTests(unittest.TestCase):
    def test_module_owns_exact_exports(self):
        pricing = importlib.import_module("ambient_codex.usage_pricing")
        self.assertEqual(pricing.__all__, MOVED_NAMES)

    def test_module_exposes_cost_math(self):
        pricing = importlib.import_module("ambient_codex.usage_pricing")
        for name in ("usage_cost", "reference_cost"):
            self.assertIn(name, pricing.__all__)
            self.assertTrue(callable(getattr(pricing, name)))

    def test_import_is_side_effect_free_in_fresh_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.usage_pricing"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class ModelPricingTests(unittest.TestCase):
    def setUp(self):
        self.pricing = importlib.import_module("ambient_codex.usage_pricing")

    def test_priced_model_returns_pair(self):
        catalog = [{"id": "x", "pricing": {"input": 3, "output": 15}}]
        self.assertEqual(self.pricing.model_pricing(catalog, "x"), (3.0, 15.0))

    def test_string_prices_are_coerced(self):
        catalog = [{"id": "x", "pricing": {"input": "1.5", "output": "6"}}]
        self.assertEqual(self.pricing.model_pricing(catalog, "x"), (1.5, 6.0))

    def test_unpriced_and_degraded_are_none(self):
        cases = [
            ([], "x"),                                             # empty
            (None, "x"),                                          # no catalog
            ([{"id": "x"}], "x"),                                 # no pricing
            ([{"id": "x", "pricing": "nope"}], "x"),             # non-dict
            ([{"id": "x", "pricing": {"input": 0, "output": 0}}], "x"),  # all-zero
            ([{"id": "x", "pricing": {"input": -1, "output": 5}}], "x"),  # negative
            ([{"id": "x", "pricing": {"input": float("nan"),
                                       "output": 5}}], "x"),      # NaN
            ([{"id": "x", "pricing": {"input": None, "output": 5}}], "x"),  # None
            ([{"id": "x", "pricing": {"input": 5, "output": -1}}], "x"),  # out neg
            ([{"id": "x", "pricing": {"input": 5,
                                       "output": float("nan")}}], "x"),  # out NaN
            ([{"id": "x", "pricing": {"input": "abc", "output": 5}}], "x"),  # bad
            ([{"id": "y", "pricing": {"input": 3, "output": 15}}], "x"),  # miss
        ]
        for catalog, model in cases:
            with self.subTest(catalog=catalog):
                self.assertIsNone(self.pricing.model_pricing(catalog, model))

    def test_partial_zero_is_still_priced_either_side(self):
        self.assertEqual(
            self.pricing.model_pricing(
                [{"id": "x", "pricing": {"input": 0, "output": 8}}], "x"),
            (0.0, 8.0))
        self.assertEqual(
            self.pricing.model_pricing(
                [{"id": "x", "pricing": {"input": 8, "output": 0}}], "x"),
            (8.0, 0.0))

    def test_first_matching_id_wins(self):
        catalog = [
            {"id": "x", "pricing": {"input": 3, "output": 15}},
            {"id": "x", "pricing": {"input": 1, "output": 1}},
        ]
        self.assertEqual(self.pricing.model_pricing(catalog, "x"), (3.0, 15.0))


class ParseReferencePriceTests(unittest.TestCase):
    def setUp(self):
        self.pricing = importlib.import_module("ambient_codex.usage_pricing")

    def test_pair_and_blended(self):
        self.assertEqual(self.pricing.parse_reference_price("3/15"), (3.0, 15.0))
        self.assertEqual(self.pricing.parse_reference_price("10"), (10.0, 10.0))
        self.assertEqual(self.pricing.parse_reference_price(" 2 / 4 "), (2.0, 4.0))

    def test_junk_returns_none(self):
        for raw in ("", "  ", "a/b", "3/", "/3", "3/4/5", "0", "-1", "1/-1",
                    "inf", "nan", "1/nan", None, 3):
            with self.subTest(raw=raw):
                self.assertIsNone(self.pricing.parse_reference_price(raw))


class UsageCostTests(unittest.TestCase):
    """Pure cost math: injected assumed-price tuple + token coercer, catalog
    lookup via the module's own ``model_pricing``."""

    def setUp(self):
        self.pricing = importlib.import_module("ambient_codex.usage_pricing")

    def test_priced_model_uses_catalog_pricing(self):
        catalog = [{"id": "m", "pricing": {"input": 3, "output": 15}}]
        cost, assumed = self.pricing.usage_cost(
            "m",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            catalog, (2.0, 8.0), _pos_int)
        self.assertFalse(assumed)
        self.assertAlmostEqual(cost, 18.0)  # (1e6*3 + 1e6*15) / 1e6

    def test_unpriced_model_falls_to_assumed_prices(self):
        cost, assumed = self.pricing.usage_cost(
            "missing",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            [], (2.0, 8.0), _pos_int)
        self.assertTrue(assumed)
        self.assertAlmostEqual(cost, 10.0)  # (1e6*2 + 1e6*8) / 1e6

    def test_zero_priced_model_is_assumed_not_free(self):
        # all-zero "pricing" is UNPRICED -> worst-case assumed, never $0.
        cost, assumed = self.pricing.usage_cost(
            "z", {"prompt_tokens": 1_000_000, "completion_tokens": 0},
            [{"id": "z", "pricing": {"input": 0, "output": 0}}],
            (2.0, 8.0), _pos_int)
        self.assertTrue(assumed)
        self.assertAlmostEqual(cost, 2.0)  # 1e6*2 / 1e6

    def test_zero_tokens_cost_zero(self):
        cost, assumed = self.pricing.usage_cost(
            "m", {}, [{"id": "m", "pricing": {"input": 3, "output": 15}}],
            (2.0, 8.0), _pos_int)
        self.assertFalse(assumed)
        self.assertEqual(cost, 0.0)

    def test_untrusted_tokens_are_coerced(self):
        # negative / non-int token fields coerce to 0 via the injected helper.
        cost, assumed = self.pricing.usage_cost(
            "m",
            {"prompt_tokens": -5, "completion_tokens": "nope"},
            [{"id": "m", "pricing": {"input": 3, "output": 15}}],
            (2.0, 8.0), _pos_int)
        self.assertFalse(assumed)
        self.assertEqual(cost, 0.0)

    def test_full_precision_small_counts(self):
        cost, assumed = self.pricing.usage_cost(
            "m", {"prompt_tokens": 1, "completion_tokens": 1},
            [{"id": "m", "pricing": {"input": 3, "output": 15}}],
            (2.0, 8.0), _pos_int)
        self.assertFalse(assumed)
        self.assertAlmostEqual(cost, 18 / 1e6)


class ReferenceCostTests(unittest.TestCase):
    def setUp(self):
        self.pricing = importlib.import_module("ambient_codex.usage_pricing")

    def test_tokens_times_reference(self):
        self.assertAlmostEqual(
            self.pricing.reference_cost(
                {"prompt_tokens": 100_000, "completion_tokens": 10_000},
                (3.0, 15.0), _pos_int),
            0.45)  # (1e5*3 + 1e4*15) / 1e6

    def test_zero_tokens_cost_zero(self):
        self.assertEqual(
            self.pricing.reference_cost({}, (3.0, 15.0), _pos_int), 0.0)

    def test_untrusted_tokens_are_coerced(self):
        self.assertEqual(
            self.pricing.reference_cost(
                {"prompt_tokens": None, "completion_tokens": -3},
                (3.0, 15.0), _pos_int),
            0.0)


class RelativeSavingsTests(unittest.TestCase):
    def setUp(self):
        self.pricing = importlib.import_module("ambient_codex.usage_pricing")
        self.catalog = [{
            "id": "m", "pricing": {"input": 1, "output": 1},
        }]

    def test_note_is_relative_only_and_omits_unpriced_models(self):
        usage = {"prompt_tokens": 100, "completion_tokens": 100}
        note = self.pricing.relative_savings_note(
            "m", usage, self.catalog, (3.0, 15.0), (20.0, 60.0),
            _pos_int)
        self.assertIn("% cheaper", note)
        self.assertNotIn("$", note)
        self.assertEqual(self.pricing.relative_savings_note(
            "missing", usage, self.catalog, (3.0, 15.0), (20.0, 60.0),
            _pos_int), "")

    def test_mixed_serving_prices_each_models_tokens(self):
        catalog = self.catalog + [{
            "id": "n", "pricing": {"input": 2, "output": 2},
        }]
        note = self.pricing.relative_savings_note_by_served(
            {"m": {"prompt_tokens": 100},
             "n": {"completion_tokens": 100, "_estimated": True}},
            catalog, (3.0, 15.0), (20.0, 60.0), _pos_int)
        self.assertIn("mixed serving, 2 models", note)
        self.assertIn("(est.)", note)
        self.assertNotIn("$", note)


class PricingFacadeTests(unittest.TestCase):
    def test_facade_wrappers_delegate(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(facade._usage_pricing, "model_pricing",
                                   return_value=(1.0, 2.0)) as mp:
                self.assertEqual(facade.model_pricing(["cat"], "m"), (1.0, 2.0))
            mp.assert_called_once_with(["cat"], "m")
            with mock.patch.object(facade._usage_pricing, "parse_reference_price",
                                   return_value=(9.0, 9.0)) as pr:
                self.assertEqual(facade.parse_reference_price("9"), (9.0, 9.0))
            pr.assert_called_once_with("9")

    def test_module_pricing_is_the_seam_for_downstream_callers(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            # usage_cost resolves pricing via the module-level model_pricing
            # seam; patching it must flow through to the downstream cost math.
            with mock.patch.object(facade._usage_pricing, "model_pricing",
                                   return_value=(2.0, 4.0)):
                cost, assumed = facade.usage_cost(
                    "m", {"prompt_tokens": 1_000_000,
                          "completion_tokens": 1_000_000})
            self.assertFalse(assumed)
            self.assertAlmostEqual(cost, 6.0)  # (1e6*2 + 1e6*4) / 1e6


class UsageCostFacadeDelegationTests(unittest.TestCase):
    """The facade wrappers delegate to the pure module, injecting the facade's
    own catalog default, worst-case assumed prices, and token coercer."""

    def test_usage_cost_wrapper_passes_facade_values(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            usage = {"prompt_tokens": 5}
            with mock.patch.object(facade._usage_pricing, "usage_cost",
                                   return_value=(1.23, True)) as uc:
                result = facade.usage_cost("m", usage)
            self.assertEqual(result, (1.23, True))
            uc.assert_called_once_with(
                "m", usage, facade._PRICING_CATALOG,
                (facade.ASSUMED_MAX_INPUT_PRICE,
                 facade.ASSUMED_MAX_OUTPUT_PRICE),
                facade._as_pos_int)

    def test_usage_cost_wrapper_forwards_explicit_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            usage = {"prompt_tokens": 5}
            explicit = [{"id": "m", "pricing": {"input": 1, "output": 2}}]
            with mock.patch.object(facade._usage_pricing, "usage_cost",
                                   return_value=(0.0, False)) as uc:
                facade.usage_cost("m", usage, catalog=explicit)
            uc.assert_called_once_with(
                "m", usage, explicit,
                (facade.ASSUMED_MAX_INPUT_PRICE,
                 facade.ASSUMED_MAX_OUTPUT_PRICE),
                facade._as_pos_int)

    def test_reference_cost_wrapper_passes_coercer(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            usage = {"prompt_tokens": 100}
            ref = (3.0, 15.0)
            with mock.patch.object(facade._usage_pricing, "reference_cost",
                                   return_value=0.42) as rc:
                result = facade.reference_cost(usage, ref)
            self.assertEqual(result, 0.42)
            rc.assert_called_once_with(usage, ref, facade._as_pos_int)


if __name__ == "__main__":
    unittest.main()
