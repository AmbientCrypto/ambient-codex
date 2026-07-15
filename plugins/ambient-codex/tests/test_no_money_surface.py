"""Founder rule: public CLI guidance must not present a monetary control."""

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ambient" / "SKILL.md"


class LargeInputFlagTests(unittest.TestCase):
    def test_skill_guidance_has_no_money_language(self):
        text = SKILL.read_text(encoding="utf-8").lower()
        for term in ("cost", "price", "pricing", "spend", "billed", "dollar", "cent"):
            self.assertIsNone(re.search(rf"\b{term}\b", text), term)

    def test_preferred_large_input_flag_is_documented_without_money_language(self):
        result = subprocess.run(
            [sys.executable, "bin/ambient", "audit", "--help"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        self.assertIn("--allow-large-input", result.stdout)
        self.assertNotIn("--allow-cost", result.stdout)

    def test_legacy_cost_flag_remains_a_compatible_hidden_alias(self):
        result = subprocess.run(
            [sys.executable, "bin/ambient", "map", "--allow-cost"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 64)
        self.assertNotIn("unrecognized arguments: --allow-cost", result.stderr)

    def test_usage_help_has_no_monetary_summary_language(self):
        result = subprocess.run(
            [sys.executable, "bin/ambient", "--help"],
            cwd=ROOT, text=True, capture_output=True, check=True,
        )
        self.assertIn("optional relative savings", result.stdout)
        self.assertNotIn("token/cost", result.stdout)
