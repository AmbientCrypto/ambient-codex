"""Dependency-wave concurrency regressions for broad Ambient builds."""

import importlib
import json
import tempfile
import threading
import unittest

from .test_build_program_hardening import _item, _plan, _record, _run


class BuildWavePolicyTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.build_workflow")

    def test_generation_wave_respects_dependencies_width_and_workstreams(self):
        batches = (
            (_item("shared/base.css", workstream="shared"),),
            (_item("site/a.html", workstream="site",
                   depends=("shared/base.css",)),),
            (_item("site/b.html", workstream="site",
                   depends=("shared/base.css",)),),
            (_item("deck/a.html", workstream="deck",
                   depends=("shared/base.css",)),),
        )
        first, pending, blocked = self.core.ready_generation_wave(
            batches, done_paths=(), failed_paths=(), width=3)
        self.assertEqual(
            tuple(item["path"] for batch in first for item in batch),
            ("shared/base.css",),
        )
        self.assertEqual(len(pending), 3)
        self.assertEqual(blocked, ())
        second, pending, blocked = self.core.ready_generation_wave(
            pending, done_paths=("shared/base.css",),
            failed_paths=(), width=3)
        self.assertEqual(
            tuple(item["path"] for batch in second for item in batch),
            ("site/a.html", "deck/a.html"),
        )
        self.assertEqual(
            tuple(item["path"] for batch in pending for item in batch),
            ("site/b.html",),
        )
        self.assertEqual(blocked, ())

    def test_generation_wave_fails_dependency_blocked_batches_without_mutation(self):
        batches = (
            (_item("site/a.html", workstream="site",
                   depends=("shared/base.css",)),),
        )
        before = repr(batches)
        ready, pending, blocked = self.core.ready_generation_wave(
            batches, done_paths=(), failed_paths=("shared/base.css",),
            width=3)
        self.assertEqual(repr(batches), before)
        self.assertEqual(ready, ())
        self.assertEqual(pending, ())
        self.assertEqual(
            blocked,
            (("site/a.html",
              "dependency generation failed: shared/base.css"),),
        )


class BuildWaveIntegrationTests(unittest.TestCase):
    def test_independent_workstreams_generate_in_parallel_after_foundation(self):
        with tempfile.TemporaryDirectory() as root:
            calls = {"count": 0, "active": 0, "maximum": 0}
            lock = threading.Lock()
            fanout = threading.Barrier(3, timeout=5)

            def complete(api_key, api_url, model, messages, args, **kwargs):
                with lock:
                    calls["count"] += 1
                    call_number = calls["count"]
                if call_number == 1:
                    return _plan([
                        _item("shared/base.css", workstream="shared"),
                        _item("site/index.html", workstream="site",
                              depends=("shared/base.css",)),
                        _item("deck/index.html", workstream="deck",
                              depends=("shared/base.css",)),
                        _item("press/release.md", workstream="press",
                              depends=("shared/base.css",)),
                    ]), {}, {"finish_reason": "stop"}
                prompt = messages[1]["content"]
                target = prompt.rsplit(
                    "GENERATE NOW — complete content for exactly these files:",
                    1,
                )[-1]
                if "shared/base.css" in target:
                    return _record("shared/base.css", "body{}"), {}, {
                        "finish_reason": "stop"}
                with lock:
                    calls["active"] += 1
                    calls["maximum"] = max(
                        calls["maximum"], calls["active"])
                try:
                    fanout.wait()
                    for path, content in (
                        ("site/index.html", "<main/>"),
                        ("deck/index.html", "<section/>"),
                        ("press/release.md", "# Release"),
                    ):
                        if path in target:
                            return _record(path, content), {}, {
                                "finish_reason": "stop"}
                    self.fail(f"unexpected generation prompt: {prompt}")
                finally:
                    with lock:
                        calls["active"] -= 1

            stdout, _stderr, code = _run(root, complete)
            envelope = json.loads(stdout)
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["failed"], [])
            self.assertEqual(calls["maximum"], 3)
            self.assertEqual(
                envelope["metrics"].get(
                    "maximum_parallel_generation_calls"),
                3,
            )


if __name__ == "__main__":
    unittest.main()
