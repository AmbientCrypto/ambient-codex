"""Phase 5 contracts for extracted audit orchestration bindings."""

import argparse
import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock


class AuditCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.audit_command")

    def test_dependency_bindings_are_immutable(self):
        deps = self.core.AuditDependencies.bind(render_result=object())

        with self.assertRaises(TypeError):
            deps.bindings["render_result"] = object()

    def test_structured_retry_is_fresh_and_never_replays_failed_output(self):
        messages = [
            {"role": "system", "content": "audit policy"},
            {"role": "user", "content": "trusted file input"},
        ]
        failed = "untrusted reasoning without a final answer"

        retry = self.core._structured_retry_messages(
            failed, True, messages, lambda _text: None)

        self.assertIsNotNone(retry)
        self.assertEqual(retry[1], messages[1])
        self.assertIn("valid JSON", retry[0]["content"])
        self.assertNotIn(failed, "".join(item["content"] for item in retry))

    def test_structured_retry_is_skipped_for_usable_or_prose_output(self):
        messages = [{"role": "system", "content": "policy"},
                    {"role": "user", "content": "input"}]
        def parse(_text):
            return {"findings": []}

        self.assertIsNone(self.core._structured_retry_messages(
            "valid", True, messages, parse))
        self.assertIsNone(self.core._structured_retry_messages(
            "prose", False, messages, lambda _text: None))

    def test_command_retries_one_unusable_structured_response(self):
        root = os.path.dirname(os.path.dirname(__file__))
        executable = os.path.join(root, "bin", "ambient")
        loader = importlib.machinery.SourceFileLoader(
            "ambient_audit_retry_contract", executable)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        ambient = importlib.util.module_from_spec(spec)
        loader.exec_module(ambient)
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "bug.py")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("def divide(a, b):\n    return a / b\n")
            args = argparse.Namespace(
                paths=[source], staged=False, diff=None, repo=None, focus=None,
                allow_secrets=False, format="json", dry_run=False,
                consensus=None, model="fake/reason", max_tokens=None,
                temperature=0.1, timeout=30, raw=False, fallback=False,
                allow_partial=False, allow_cost=True, yes=True, no_cache=True,
                cache_ttl=None, parallel=None, reduce_model=None, json=True,
                deep=None, best_of=None)
            catalog = [{
                "id": "fake/reason", "context_length": 131_072,
                "max_output_length": 32_768, "is_ready": True,
                "supported_features": ["reasoning"],
                "output_modalities": ["text"],
            }]
            valid = json.dumps({
                "findings": [{"severity": "HIGH", "file": "bug.py",
                              "line": 2, "title": "division by zero",
                              "scenario": "b=0 raises"}],
                "verdict": "FIX FIRST",
            })
            first = "untrusted reasoning without a final answer"
            completions = mock.Mock(side_effect=[
                (first, {}, {"finish_reason": "stop"}),
                (valid, {}, {"finish_reason": "stop"}),
            ])
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(ambient, "safe_catalog",
                                   return_value=catalog), \
                    mock.patch.object(ambient, "complete", completions), \
                    contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                ambient.cmd_audit(args, "key-abcdef123456", "https://x", {})

        envelope = json.loads(stdout.getvalue())
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(completions.call_args_list), 2)
        retry_messages = completions.call_args_list[1].args[3]
        self.assertNotIn(
            first, "".join(item["content"] for item in retry_messages))
        self.assertIn("retrying once", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
