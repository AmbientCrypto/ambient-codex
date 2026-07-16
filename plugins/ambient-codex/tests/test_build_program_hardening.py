"""Regression contracts from the broad creative-showcase build incident."""

import argparse
import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import random
import shutil
import tempfile
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(os.path.dirname(_HERE), "bin", "ambient")
_KEY = "sk-test-key-abcdef1234567890"
_CATALOG = [{
    "id": "reasoner/model",
    "context_length": 200_000,
    "max_output_length": 200_000,
    "is_ready": True,
    "supported_features": ["reasoning", "structured_outputs"],
    "output_modalities": ["text"],
    "pricing": {"input": 1.0, "output": 4.0},
}]


def _load_cli():
    loader = importlib.machinery.SourceFileLoader(
        "ambient_cli_build_program_hardening", _BIN)
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(
            "ambient_cli_build_program_hardening", loader))
    loader.exec_module(module)
    return module


amb = _load_cli()


@contextlib.contextmanager
def _patched(**attrs):
    old, missing = {}, object()
    for name, value in attrs.items():
        old[name] = getattr(amb, name, missing)
        setattr(amb, name, value)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is missing:
                delattr(amb, name)
            else:
                setattr(amb, name, value)


def _args(root, **overrides):
    values = {
        "task": ["build", "a", "broad", "creative", "showcase"],
        "dir": root,
        "context": None,
        "apply": True,
        "force": False,
        "plan_only": False,
        "dry_run": False,
        "max_files": 32,
        "max_file_bytes": 200_000,
        "no_resume": True,
        "json": True,
        "allow_secrets": False,
        "model": "reasoner/model",
        "max_tokens": 10_000,
        "temperature": 0.1,
        "timeout": 30,
        "raw": False,
        "fallback": False,
        "allow_partial": False,
        "allow_cost": True,
        "yes": True,
        "no_cache": True,
        "cache_ttl": None,
        "parallel": None,
        "system": None,
        "response_format": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _plan(items, *, brief="Keep every name, route, and visual token stable."):
    return json.dumps({
        "plan": items,
        "project_brief": brief,
        "notes": "A coherent multi-workstream program.",
        "advisory_steps": [],
    })


def _item(path, *, lines=40, size=2_000, workstream="shared", depends=()):
    return {
        "path": path,
        "purpose": f"Create {path}",
        "est_lines": lines,
        "est_bytes": size,
        "workstream": workstream,
        "depends_on": list(depends),
    }


def _record(path, content):
    return json.dumps({"path": path, "content": content})


def _run(root, complete):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with _patched(
        complete=complete,
        safe_catalog=lambda *args, **kwargs: _CATALOG,
        cost_gate=lambda *args, **kwargs: None,
        cost_gate_mr=lambda *args, **kwargs: None,
        _gate_amount=lambda *args, **kwargs: None,
        warn_if_stdin_ignored=lambda *args, **kwargs: None,
    ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = 0
        try:
            amb.cmd_build(_args(root), _KEY, "https://example.invalid", {})
        except SystemExit as error:
            exit_code = error.code if isinstance(error.code, int) else 1
    return stdout.getvalue(), stderr.getvalue(), exit_code


class BuildPlanningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.build_workflow")

    def test_artifact_estimates_are_conservative_for_dense_web_assets(self):
        target = self.core.artifact_target_bytes(
            max_tokens=10_000, chars_per_token=3.2,
            max_file_bytes=200_000, is_reasoning=True,
            structured_output=True)
        self.assertGreaterEqual(target, 8_000)
        self.assertLess(target, 20_000)
        self.assertGreaterEqual(
            self.core.estimated_item_bytes(
                {"path": "assets/site.css", "est_lines": 420}),
            420 * 96,
        )
        self.assertGreater(
            self.core.estimated_item_bytes({
                "path": "bad.css",
                "est_lines": 20,
                "est_bytes": float("inf"),
            }),
            0,
        )

    def test_artifact_capacity_adapts_to_live_model_profile(self):
        glm_target = self.core.artifact_target_bytes(
            max_tokens=10_372, chars_per_token=3.2,
            max_file_bytes=200_000, is_reasoning=True,
            structured_output=True)
        kimi_target = self.core.artifact_target_bytes(
            max_tokens=65_536, chars_per_token=3.2,
            max_file_bytes=200_000, is_reasoning=True,
            structured_output=True)
        direct_target = self.core.artifact_target_bytes(
            max_tokens=10_372, chars_per_token=3.2,
            max_file_bytes=200_000, is_reasoning=False,
            structured_output=True)

        self.assertGreater(kimi_target, glm_target * 3)
        self.assertGreater(direct_target, glm_target)
        self.assertLessEqual(kimi_target, 200_000)

    def test_every_catalog_model_gets_a_bounded_metadata_driven_target(self):
        catalog_path = os.path.join(_HERE, "catalog.json")
        with open(catalog_path, encoding="utf-8") as handle:
            models = json.load(handle)["data"]

        targets = {}
        for model in models:
            features = set(model.get("supported_features") or [])
            max_tokens = min(
                int(model.get("max_output_length") or 16_384),
                65_536,
            )
            target = self.core.artifact_target_bytes(
                max_tokens=max_tokens,
                chars_per_token=3.2,
                max_file_bytes=200_000,
                is_reasoning="reasoning" in features,
                structured_output=bool(
                    {"structured_outputs", "json_mode"} & features),
            )
            self.assertGreater(target, 0, model["id"])
            self.assertLessEqual(target, 200_000, model["id"])
            targets[model["id"]] = target

        self.assertGreater(
            targets["moonshotai/kimi-k2.7-code"],
            targets["qwen/qwen3-coder-30b-a3b-instruct"],
        )

    def test_generation_budget_includes_visible_artifact_not_only_reasoning(self):
        tokens = self.core.generation_output_tokens(
            estimated_bytes=60_000, chars_per_token=3.2,
            is_reasoning=True, max_tokens=65_536)
        self.assertGreater(tokens, 60_000 / 3.2)
        self.assertGreater(
            tokens,
            self.core.generation_output_tokens(
                estimated_bytes=60_000, chars_per_token=3.2,
                is_reasoning=True, max_tokens=10_000),
        )

    def test_plan_contract_exposes_omissions_duplicates_and_oversized_files(self):
        items = [
            _item("shared/base.css"),
            _item("shared/base.css"),
            _item("deck/index.html", size=80_000),
            _item("site/index.html"),
        ]
        result = self.core.assess_plan_contract(
            items, max_files=3, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)

        self.assertEqual(
            tuple(item["path"] for item in result["omitted"]),
            ("site/index.html",),
        )
        self.assertIn("shared/base.css", result["duplicate_paths"])
        self.assertEqual(
            tuple(item["path"] for item in result["oversized"]),
            ("deck/index.html",),
        )
        self.assertFalse(result["complete"])

    def test_plan_contract_never_silently_discards_non_object_items(self):
        result = self.core.assess_plan_contract(
            [_item("a.txt"), "not-an-object"],
            max_files=8, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)
        self.assertFalse(result["complete"])
        self.assertIn(
            "plan item must be an object",
            {item["reason"] for item in result["failures"]},
        )

    def test_plan_contract_bounds_omission_details_but_keeps_exact_count(self):
        items = [_item(f"file-{index}.txt") for index in range(1_000)]
        result = self.core.assess_plan_contract(
            items, max_files=1, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)
        self.assertEqual(result["omitted_count"], 999)
        self.assertLessEqual(len(result["omitted"]), 100)
        self.assertFalse(result["complete"])

    def test_plan_contract_fuzz_is_bounded_immutable_and_never_crashes(self):
        rng = random.Random(20260716)
        odd_values = [
            None, True, False, -1, 0, 1, "12", "bad",
            float("inf"), [], {}, 10**100,
        ]
        for iteration in range(500):
            items = []
            for index in range(rng.randrange(0, 30)):
                if rng.random() < 0.15:
                    items.append(rng.choice(odd_values))
                    continue
                items.append({
                    "path": f"group-{index % 4}/file-{index}.txt",
                    "purpose": rng.choice(odd_values),
                    "est_lines": rng.choice(odd_values),
                    "est_bytes": rng.choice(odd_values),
                    "workstream": rng.choice(odd_values),
                    "depends_on": rng.choice([
                        [],
                        ["group-0/file-0.txt"],
                        odd_values,
                        "not-a-list",
                    ]),
                })
            before = repr(items)
            result = self.core.assess_plan_contract(
                items, max_files=8, max_file_bytes=200_000,
                target_file_bytes=16_000, root="/build",
                safe_relpath=lambda path, root: str(path))
            self.assertEqual(repr(items), before, iteration)
            self.assertLessEqual(len(result["plan"]), 8, iteration)
            self.assertLessEqual(len(result["omitted"]), 100, iteration)

    def test_plan_contract_orders_dependencies_without_mutating_input(self):
        items = [
            _item("site/index.html", workstream="site",
                  depends=("shared/base.css",)),
            _item("shared/base.css", workstream="shared"),
        ]
        before = json.loads(json.dumps(items))
        result = self.core.assess_plan_contract(
            items, max_files=8, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)

        self.assertEqual(items, before)
        self.assertEqual(
            tuple(item["path"] for item in result["plan"]),
            ("shared/base.css", "site/index.html"),
        )
        self.assertTrue(result["complete"])

    def test_plan_contract_rejects_cycles_and_dependency_blocked_artifacts(self):
        cycle = self.core.assess_plan_contract(
            [
                _item("a.txt", depends=("b.txt",)),
                _item("b.txt", depends=("a.txt",)),
            ],
            max_files=8, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)
        self.assertEqual(set(cycle["cycles"]), {"a.txt", "b.txt"})
        self.assertFalse(cycle["complete"])

        blocked = self.core.assess_plan_contract(
            [
                _item("large.css", size=80_000),
                _item("page.html", depends=("large.css",)),
            ],
            max_files=8, max_file_bytes=200_000,
            target_file_bytes=16_000, root="/build",
            safe_relpath=lambda path, root: path)
        self.assertEqual(
            tuple(item["path"] for item in blocked["runnable_plan"]),
            (),
        )
        self.assertEqual(
            blocked["blocked_dependencies"][0]["path"], "page.html")

    def test_generation_batches_preserve_workstream_boundaries(self):
        plan = [
            _item("shared/a.css", workstream="shared", size=2_000),
            _item("site/a.html", workstream="site", size=2_000),
            _item("site/b.html", workstream="site", size=2_000),
        ]
        batches, _ = self.core.generation_batches(
            plan, done_paths=(), max_tokens=10_000,
            chars_per_token=3.2, target_batch_bytes=8_000)

        self.assertEqual(
            tuple(tuple(item["path"] for item in batch) for batch in batches),
            (("shared/a.css",), ("site/a.html", "site/b.html")),
        )

    def test_reasoning_batch_width_is_bounded_independently_of_byte_capacity(self):
        plan = [
            _item(f"assets/{index}.svg", workstream="assets", size=1_000)
            for index in range(8)
        ]
        batches, _ = self.core.generation_batches(
            plan, done_paths=(), max_tokens=65_536,
            chars_per_token=3.2, target_batch_bytes=100_000,
            max_batch_files=3)
        self.assertEqual(
            tuple(len(batch) for batch in batches),
            (3, 3, 2),
        )

    def test_multifile_recovery_flattens_directly_to_focused_files(self):
        batch = tuple(_item(f"{name}.txt") for name in ("a", "b", "c", "d"))
        retries, _attempts, _recovery, _failures = (
            self.core.generation_recovery(
                batch, done_paths=(), failed_paths=(),
                response_meta={"finish_reason": "length"}, attempts={},
                recovery_paths=()))
        self.assertEqual(
            tuple(tuple(item["path"] for item in part) for part in retries),
            (("a.txt",), ("b.txt",), ("c.txt",), ("d.txt",)),
        )

    def test_broad_plans_prefer_json_object_before_strict_schema(self):
        ladder = (
            {"type": "json_schema"},
            {"type": "json_object"},
            None,
        )
        self.assertEqual(
            self.core.prioritize_plan_formats(ladder, input_chars=2_000),
            ({"type": "json_object"}, {"type": "json_schema"}, None),
        )
        self.assertEqual(
            self.core.prioritize_plan_formats(ladder, input_chars=100),
            ladder,
        )

    def test_recovery_prompt_retains_project_brief(self):
        plan = (_item("deck/index.html", workstream="deck"),)
        prompt = self.core.generation_prompt(
            task="build showcase", batch=plan, plan=plan, done_paths=(),
            context="", system_chars=100, single_shot_chars=10_000,
            recovery_paths=("deck/index.html",),
            project_brief="Client name is Fernwell. Never rename it.")

        self.assertIn("Fernwell", prompt)
        self.assertIn("RECOVERY GENERATION", prompt)

    def test_completion_policy_can_disable_inner_budget_escalation(self):
        state = importlib.import_module("ambient_codex.session_state")
        self.assertEqual(state.budget_escalation_limit(0), 0)


class BuildProgramIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_oversized_plan_is_refined_before_generation(self):
        calls = {"count": 0}

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([
                    _item("deck/index.html", lines=900, size=90_000,
                          workstream="deck"),
                ]), {}, {"finish_reason": "stop"}
            if calls["count"] == 2:
                return _plan([
                    _item("deck/slides.css", workstream="deck"),
                    _item("deck/slide-01.html", workstream="deck",
                          depends=("deck/slides.css",)),
                ]), {}, {"finish_reason": "stop"}
            requested = messages[1]["content"]
            if requested.rfind("deck/slides.css") > requested.rfind(
                    "deck/slide-01.html"):
                return _record("deck/slides.css", "body{}"), {}, {
                    "finish_reason": "stop"}
            return _record("deck/slide-01.html", "<section/>"), {}, {
                "finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)

        self.assertEqual(code, 0, envelope)
        self.assertEqual(envelope["failed"], [])
        self.assertGreaterEqual(calls["count"], 4)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "deck", "slide-01.html")))

    def test_plan_cap_overflow_is_never_reported_clean(self):
        calls = {"count": 0}
        too_many = [
            _item("a.txt"), _item("b.txt"), _item("c.txt"),
        ]

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            return _plan(too_many), {}, {"finish_reason": "stop"}

        stdout = io.StringIO()
        with _patched(
            complete=complete,
            safe_catalog=lambda *args, **kwargs: _CATALOG,
            cost_gate=lambda *args, **kwargs: None,
            warn_if_stdin_ignored=lambda *args, **kwargs: None,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = 0
            try:
                amb.cmd_build(
                    _args(self.root, max_files=2, plan_only=True),
                    _KEY, "https://example.invalid", {})
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 1

        envelope = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertTrue(envelope["partial"])
        self.assertTrue(envelope["coverage_gap"])
        self.assertGreaterEqual(calls["count"], 2)

    def test_headless_apply_makes_each_completed_batch_visible_immediately(self):
        calls = {"count": 0}

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([
                    _item("shared/base.css", workstream="shared"),
                    _item("site/index.html", workstream="site"),
                ]), {}, {"finish_reason": "stop"}
            if calls["count"] == 2:
                return _record("shared/base.css", "body{}"), {}, {
                    "finish_reason": "stop"}
            self.assertTrue(os.path.exists(
                os.path.join(self.root, "shared", "base.css")))
            return _record("site/index.html", "<main/>"), {}, {
                "finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        self.assertEqual(code, 0, stdout)

    def test_build_recovery_disables_completion_level_budget_amplification(self):
        calls = {"count": 0}
        policies = []

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([_item("site/index.html")]), {}, {
                    "finish_reason": "stop"}
            policies.append(args.max_budget_escalations)
            if calls["count"] == 2:
                return "reasoning only", {}, {
                    "reasoning_draft": True,
                    "salvaged_partial": True,
                }
            return _record("site/index.html", "<main/>"), {}, {
                "finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 0, envelope)
        self.assertEqual(policies, [1, 0])

    def test_terminal_batch_error_is_not_multiplied_by_sharding(self):
        calls = {"count": 0}

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([
                    _item("site/a.html", workstream="site"),
                    _item("site/b.html", workstream="site"),
                ]), {}, {"finish_reason": "stop"}
            raise amb.ChatError("rate", "slow down")

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 2)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(
            {item["path"] for item in envelope["failed"]},
            {"site/a.html", "site/b.html"},
        )

    def test_terminal_error_aborts_remaining_workstreams(self):
        calls = {"count": 0}

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([
                    _item("brand/a.css", workstream="brand"),
                    _item("site/a.html", workstream="site"),
                    _item("deck/a.html", workstream="deck"),
                ]), {}, {"finish_reason": "stop"}
            raise amb.ChatError("funds", "no balance")

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 2)
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(envelope["failed"]), 3)

    def test_focused_file_generation_uses_structured_output_when_supported(self):
        calls = {"count": 0}
        response_formats = []

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([_item("site/index.html")]), {}, {
                    "finish_reason": "stop"}
            response_formats.append(args.response_format)
            return json.dumps({
                "files": [{
                    "path": "site/index.html",
                    "content": "<main/>",
                }],
            }), {}, {"finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        self.assertEqual(code, 0, stdout)
        self.assertEqual(
            response_formats[0]["type"], "json_schema")

    def test_structured_output_rejection_downgrades_on_focused_retry(self):
        calls = {"count": 0}
        response_formats = []

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([_item("site/index.html")]), {}, {
                    "finish_reason": "stop"}
            response_formats.append(args.response_format)
            if args.response_format:
                raise amb.ChatError(
                    "unknown", "response_format was rejected")
            return _record("site/index.html", "<main/>"), {}, {
                "finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 0, envelope)
        self.assertEqual(response_formats[0]["type"], "json_schema")
        self.assertIsNone(response_formats[1])

    def test_focused_file_uses_the_already_budgeted_second_compact_retry(self):
        calls = {"count": 0}
        policies = []

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([_item("site/index.html")]), {}, {
                    "finish_reason": "stop"}
            policies.append(args.max_budget_escalations)
            if calls["count"] < 4:
                return "", {}, {"finish_reason": "stop"}
            return _record("site/index.html", "<main/>"), {}, {
                "finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 0, envelope)
        self.assertEqual(envelope["failed"], [])
        self.assertEqual(policies, [1, 0, 0])

    def test_repeated_empty_outputs_trip_a_fast_build_circuit_breaker(self):
        calls = {"count": 0}

        def complete(api_key, api_url, model, messages, args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _plan([
                    _item(f"stream-{index}/file.txt",
                          workstream=f"stream-{index}")
                    for index in range(6)
                ]), {}, {"finish_reason": "stop"}
            return "", {}, {"finish_reason": "stop"}

        stdout, _stderr, code = _run(self.root, complete)
        envelope = json.loads(stdout)
        self.assertEqual(code, 2)
        self.assertLessEqual(calls["count"], 4)
        self.assertEqual(len(envelope["failed"]), 6)
        self.assertEqual(
            envelope["metrics"].get("circuit_breaker_trips"), 1)

    def test_build_caps_reject_pathological_resource_requests_before_network(self):
        for overrides in (
                {"max_files": 513},
                {"max_file_bytes": 5_000_001}):
            with self.subTest(overrides=overrides), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    amb.cmd_build(
                        _args(self.root, **overrides),
                        _KEY, "https://example.invalid", {})
            self.assertEqual(raised.exception.code, 64)

    def test_cancellation_keeps_applied_files_and_resume_skips_completed_work(self):
        first_calls = {"count": 0}

        def interrupted(api_key, api_url, model, messages, args, **kwargs):
            first_calls["count"] += 1
            if first_calls["count"] == 1:
                return _plan([
                    _item("shared/base.css", workstream="shared"),
                    _item("site/index.html", workstream="site"),
                ]), {}, {"finish_reason": "stop"}
            if first_calls["count"] == 2:
                return _record("shared/base.css", "body{}"), {}, {
                    "finish_reason": "stop"}
            raise KeyboardInterrupt

        with _patched(
            complete=interrupted,
            safe_catalog=lambda *args, **kwargs: _CATALOG,
            cost_gate=lambda *args, **kwargs: None,
            cost_gate_mr=lambda *args, **kwargs: None,
            _gate_amount=lambda *args, **kwargs: None,
            warn_if_stdin_ignored=lambda *args, **kwargs: None,
        ), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                amb.cmd_build(
                    _args(self.root), _KEY,
                    "https://example.invalid", {})

        self.assertTrue(os.path.exists(
            os.path.join(self.root, "shared", "base.css")))
        with open(
                os.path.join(self.root, ".ambient-build.json"),
                encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(set(saved["done"]), {"shared/base.css"})

        resumed_targets = []

        def resumed(api_key, api_url, model, messages, args, **kwargs):
            requested = messages[1]["content"]
            resumed_targets.append(requested)
            return _record("site/index.html", "<main/>"), {}, {
                "finish_reason": "stop"}

        stdout = io.StringIO()
        with _patched(
            complete=resumed,
            safe_catalog=lambda *args, **kwargs: _CATALOG,
            cost_gate=lambda *args, **kwargs: None,
            cost_gate_mr=lambda *args, **kwargs: None,
            _gate_amount=lambda *args, **kwargs: None,
            warn_if_stdin_ignored=lambda *args, **kwargs: None,
        ), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(io.StringIO()):
            amb.cmd_build(
                _args(self.root, no_resume=False), _KEY,
                "https://example.invalid", {})

        envelope = json.loads(stdout.getvalue())
        self.assertEqual(envelope["failed"], [])
        self.assertEqual(len(resumed_targets), 1)
        self.assertGreater(
            resumed_targets[0].rfind("site/index.html"),
            resumed_targets[0].rfind("shared/base.css"),
        )


if __name__ == "__main__":
    unittest.main()
