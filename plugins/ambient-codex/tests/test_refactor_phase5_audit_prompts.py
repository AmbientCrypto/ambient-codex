"""Contracts for immutable audit prompt ownership."""

import importlib
import unittest


class AuditPromptTests(unittest.TestCase):
    def test_system_prompt_retains_complete_review_contract(self):
        prompts = importlib.import_module("ambient_codex.audit_prompts")
        prompt = prompts.AUDIT_SYSTEM_PROMPT

        for marker in (
                "SEVERITY RUBRIC", "confidence", "LINE NUMBERS",
                "FORMAT", "concrete failure\nscenario", "suggested fix",
                "never style nits", "SHIP / FIX FIRST / NEEDS WORK",
                "EXAMPLE"):
            self.assertIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
