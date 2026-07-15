"""Founder hard rule (2026-07-14): the cost/savings display is OFF by default,
NEVER shows an absolute money figure (no $ / cents), and shows ONLY a relative
%-difference when the user opts in via the `savings` setting."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"


def load_module():
    loader = importlib.machinery.SourceFileLoader("ambient_savings_hr", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


amb = load_module()

FAKE_CATALOG = [{"id": "cheap/model", "pricing": {"input": 0.10, "output": 0.30}}]
USAGE = {"prompt_tokens": 100_000, "completion_tokens": 10_000}
REF_CONF = {"AMBIENT_REFERENCE_PRICE": "3/15"}
ON_CONF = {"AMBIENT_REFERENCE_PRICE": "3/15", "AMBIENT_SAVINGS": "on"}


@contextlib.contextmanager
def savings_cache(value):
    """Force the memoized conf=None gate (receipt/render path) on or off."""
    prev = amb._SAVINGS_CACHE
    amb._SAVINGS_CACHE = value
    try:
        yield
    finally:
        amb._SAVINGS_CACHE = prev


@contextlib.contextmanager
def no_savings_env():
    prev = os.environ.pop("AMBIENT_SAVINGS", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["AMBIENT_SAVINGS"] = prev


class SavingsNoteGateTests(unittest.TestCase):
    def test_off_by_default_returns_empty(self):
        with no_savings_env():
            self.assertEqual(
                amb.savings_note("cheap/model", USAGE, FAKE_CATALOG, REF_CONF),
                "")

    def test_by_served_off_by_default_returns_empty(self):
        with no_savings_env():
            self.assertEqual(
                amb.savings_note_by_served(
                    {"cheap/model": USAGE}, FAKE_CATALOG, REF_CONF),
                "")

    def test_opted_in_shows_percent_only_never_money(self):
        with no_savings_env():
            note = amb.savings_note("cheap/model", USAGE, FAKE_CATALOG, ON_CONF)
        self.assertIn("cheaper", note)
        self.assertIn("%", note)
        self.assertNotIn("$", note)
        self.assertNotIn("¢", note)

    def test_enabled_values_are_on_1_true(self):
        for on in ("on", "1", "true", "ON", "True"):
            with self.subTest(on=on):
                conf = {**REF_CONF, "AMBIENT_SAVINGS": on}
                self.assertIn(
                    "cheaper",
                    amb.savings_note("cheap/model", USAGE, FAKE_CATALOG, conf))
        for off in ("off", "0", "false", "no", "", "yes"):
            with self.subTest(off=off):
                conf = {**REF_CONF, "AMBIENT_SAVINGS": off}
                self.assertEqual(
                    "",
                    amb.savings_note("cheap/model", USAGE, FAKE_CATALOG, conf))


class UsageCommandGateTests(unittest.TestCase):
    def _run(self, *, savings, as_json):
        with tempfile.TemporaryDirectory() as td:
            up = str(Path(td) / "usage.jsonl")
            with open(up, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": int(__import__("time").time()), "model": "cheap/model",
                    "in": 100_000, "out": 10_000, "cost": 0.004,
                    "ref": [3.0, 15.0]}) + "\n")
            out = io.StringIO()
            with self._patched_usage(up, savings), \
                    contextlib.redirect_stdout(out):
                args = argparse.Namespace(days=30, json=as_json)
                amb.cmd_usage(args)
            return out.getvalue()

    @contextlib.contextmanager
    def _patched_usage(self, up, savings):
        prev_path = amb.USAGE_PATH
        prev_read = amb.read_config_file
        prev_fetch = amb.fetch_models
        amb.USAGE_PATH = up
        conf = {"AMBIENT_SAVINGS": "on"} if savings else {}
        amb.read_config_file = lambda: dict(conf)
        amb.fetch_models = lambda *a, **k: FAKE_CATALOG
        try:
            yield
        finally:
            amb.USAGE_PATH = prev_path
            amb.read_config_file = prev_read
            amb.fetch_models = prev_fetch

    def test_text_off_shows_tokens_no_savings_no_money(self):
        with no_savings_env():
            text = self._run(savings=False, as_json=False)
        self.assertIn("cheap/model", text)
        self.assertIn("calls", text)
        self.assertNotIn("cheaper", text)
        self.assertNotIn("%", text)
        self.assertNotIn("$", text)

    def test_text_on_shows_percent_no_money(self):
        with no_savings_env():
            text = self._run(savings=True, as_json=False)
        self.assertIn("cheaper", text)
        self.assertNotIn("$", text)

    def test_json_off_nulls_saved_pct_and_never_money(self):
        with no_savings_env():
            payload = json.loads(self._run(savings=False, as_json=True))
        self.assertIsNone(payload["saved_pct"])
        self.assertTrue(all(m["saved_pct"] is None for m in payload["models"]))
        self.assertNotIn("$", json.dumps(payload))


class ReceiptGateTests(unittest.TestCase):
    def test_receipt_off_by_default_keeps_tokens_drops_savings(self):
        args = argparse.Namespace(allow_partial=False)
        err = io.StringIO()
        with no_savings_env(), savings_cache(False), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            prev = amb._PRICING_CATALOG
            amb._PRICING_CATALOG = FAKE_CATALOG
            try:
                amb.render_result("hi", False, None, args, "sk-test-key-x",
                                  usage=USAGE, model="cheap/model")
            finally:
                amb._PRICING_CATALOG = prev
        receipt = err.getvalue()
        self.assertIn("in=100000", receipt)   # token receipt still shown
        self.assertNotIn("cheaper", receipt)  # savings suppressed by default
        self.assertNotIn("$", receipt)


class SavingsKnobTests(unittest.TestCase):
    def test_savings_is_a_registered_off_default_bool_knob(self):
        knob = amb._CONFIG_BY_NAME.get("savings")
        self.assertIsNotNone(knob)
        self.assertEqual(knob["env"], "AMBIENT_SAVINGS")
        self.assertEqual(knob["default"], "off")
        self.assertEqual(knob["how"], "on|off")
        # its current() reads off with an empty config
        self.assertEqual(knob["current"]({}), "off")
        self.assertEqual(knob["current"]({"AMBIENT_SAVINGS": "on"}), "on")

    def test_norm_bool_accepts_on_off(self):
        self.assertEqual(amb._config_norm_bool("on"), "on")
        self.assertEqual(amb._config_norm_bool("off"), "off")


class ProviderUsageLeakTests(unittest.TestCase):
    def test_json_envelope_strips_provider_cost_and_savings(self):
        out = io.StringIO()
        with no_savings_env(), savings_cache(False), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            amb.emit_json(
                "ask", model="cheap/model",
                usage={"prompt_tokens": 10, "completion_tokens": 5,
                       "cost": 0.004, "saved_pct": 88, "price": 3.0,
                       "_estimated": True},
                exit_now=False)
        payload = json.loads(out.getvalue())
        self.assertEqual(
            payload["usage"],
            {"prompt_tokens": 10, "completion_tokens": 5, "_estimated": True})
        self.assertNotIn("saved_pct", out.getvalue())
        self.assertNotIn("$", out.getvalue())

    def test_public_usage_helper_allowlists_tokens_only(self):
        self.assertEqual(
            amb._public_usage({"prompt_tokens": 1, "completion_tokens": 2,
                               "cost": 9.9, "saved_pct": 50, "price": 3.0}),
            {"prompt_tokens": 1, "completion_tokens": 2})
        self.assertEqual(amb._public_usage(None), None)


if __name__ == "__main__":
    unittest.main()
