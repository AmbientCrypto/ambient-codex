"""Phase 3E contracts for immutable telemetry cache derivation."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest


class TelemetryTests(unittest.TestCase):
    def test_module_owns_observed_cpt_export(self):
        core = importlib.import_module("ambient_codex.telemetry")
        self.assertEqual(core.__all__, ("observed_cpt",))

    def test_reads_once_and_clamps_real_usage_samples(self):
        core = importlib.import_module("ambient_codex.telemetry")
        with tempfile.TemporaryDirectory() as td:
            usage_path = Path(td) / "usage.jsonl"
            usage_path.write_text("\n".join((
                json.dumps({"model": "m", "chars": 100, "in": 25}),
                json.dumps({"model": "m", "chars": 4_000, "in": 10}),
                json.dumps({"model": "m", "chars": 100, "in": 10, "est": True}),
            )), encoding="utf-8")
            value, cache = core.observed_cpt("m", True, str(usage_path), None,
                                             1.0, 8.0, 0.3)
        self.assertAlmostEqual(value, 5.2)
        self.assertEqual(cache["m"], value)
