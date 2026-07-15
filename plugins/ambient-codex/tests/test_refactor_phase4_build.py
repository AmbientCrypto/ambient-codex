"""Phase 4 contracts for resumable build workflow policies."""

import copy
import importlib
import os
import re
import tempfile
import unittest


class BuildWorkflowTests(unittest.TestCase):
    def test_safe_relative_path_rejects_escapes_and_credential_names(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        with tempfile.TemporaryDirectory() as root:
            matcher = re.compile(r"secret", re.IGNORECASE)
            self.assertEqual(
                core.safe_relative_path("src/app.py", root, secret_name_re=matcher),
                "src/app.py",
            )
            for candidate in ("../escape.py", "/tmp/escape.py", "keys/secret.txt"):
                with self.assertRaises(ValueError, msg=candidate):
                    core.safe_relative_path(candidate, root, secret_name_re=matcher)

    def test_state_path_is_scoped_to_the_build_root(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        self.assertEqual(
            core.state_path(os.path.join("tmp", "build")),
            os.path.join("tmp", "build", ".ambient-build.json"),
        )

    def test_identity_is_path_order_independent_and_version_sensitive(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        args = dict(runtime_version="1", task="make", model="m", reduce_model=None,
                    raw_context_sha="x", max_files=2, max_file_bytes=3,
                    max_tokens=4, temperature=0)
        self.assertEqual(core.resume_identity(context_paths=["b", "a"], **args),
                         core.resume_identity(context_paths=["a", "b"], **args))

    def test_resume_state_normalization_is_bounded_and_immutable(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        state = {
            "version": 1,
            "task_sha": "task",
            "plan": [{"path": "keep.py"}, {"path": "drop.py"}],
            "done": {
                "keep.py": {"content": "ok", "sha256": "ignored"},
                "drop.py": {"content": "too long", "sha256": "ignored"},
                "other.py": {"content": "unplanned", "sha256": "ignored"},
            },
            "failed": [None, {"path": "keep.py", "reason": "retry"}],
        }
        digest = __import__("hashlib").sha256(b"ok").hexdigest()
        state["done"]["keep.py"]["sha256"] = digest
        state["done"]["drop.py"]["sha256"] = __import__("hashlib").sha256(
            b"too long").hexdigest()
        before = __import__("copy").deepcopy(state)

        out = core.normalize_resume_state(
            state, task_sha="task", root="/build", max_plan=1,
            max_file_bytes=2, safe_relpath=lambda path, root: path)

        self.assertEqual(state, before)
        self.assertEqual(out["plan"], [{"path": "keep.py"}])
        self.assertEqual(out["done"], {"keep.py": {"content": "ok", "sha256": digest}})
        self.assertEqual(out["failed"], [{"path": "keep.py", "reason": "retry"}])

    def test_resume_state_normalization_rejects_bad_shape_hash_and_unsafe_path(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        base = {"version": 1, "task_sha": "task", "plan": [{"path": "a.py"}],
                "done": {}, "failed": []}
        self.assertIsNone(core.normalize_resume_state(
            {"version": 2}, task_sha="task", root="/build", max_plan=1,
            max_file_bytes=10, safe_relpath=lambda path, root: path))
        bad_hash = dict(base, done={"a.py": {"content": "ok", "sha256": "bad"}})
        self.assertIsNone(core.normalize_resume_state(
            bad_hash, task_sha="task", root="/build", max_plan=1,
            max_file_bytes=10, safe_relpath=lambda path, root: path))
        with self.assertRaises(ValueError):
            core.normalize_resume_state(
                base, task_sha="task", root="/build", max_plan=1,
                max_file_bytes=10,
                safe_relpath=lambda path, root: (_ for _ in ()).throw(ValueError("unsafe")))

    def test_plan_validation_caps_paths_and_does_not_mutate_model_payload(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        proposed = [
            {"path": "safe.py", "purpose": "keep"},
            {"path": "../unsafe.py", "purpose": "reject"},
            "not-an-object",
            {"path": "later.py"},
        ]
        before = __import__("copy").deepcopy(proposed)

        plan, rejected = core.validate_plan_items(
            proposed, max_files=3, root="/build",
            safe_relpath=lambda path, root: (
                (_ for _ in ()).throw(ValueError("escape"))
                if path.startswith("../") else path))

        self.assertEqual(proposed, before)
        self.assertEqual(plan, [{"path": "safe.py", "purpose": "keep"}])
        self.assertEqual(rejected, [{"path": "../unsafe.py", "reason": "unsafe path: escape"}])

    def test_file_record_parser_keeps_complete_records_and_drops_cut_tail(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        records = core.parse_file_records(
            '{"path":"a.py","content":"A"}\n'
            '{"path":"b.py","content":"B')
        self.assertEqual(records, [{"path": "a.py", "content": "A"}])
        wrapped = core.parse_file_records(
            '{"files":[{"path":"c.py","content":"C"}]}')
        self.assertEqual(wrapped, [{"path": "c.py", "content": "C"}])

    def test_file_record_classification_is_bounded_immutable_and_salvage_safe(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        records = [
            {"path": "a.py", "content": "A"},
            {"path": "extra.py", "content": "extra"},
            {"path": "../escape.py", "content": "unsafe"},
            {"path": "large.py", "content": "12345"},
            {"path": "a.py", "content": "duplicate"},
            {"path": "tail.py", "content": "T"},
        ]
        before = copy.deepcopy(records)

        accepted, failures, dropped = core.classify_file_records(
            records, wanted_paths=("a.py", "large.py", "tail.py"),
            plan_paths=("a.py", "large.py", "tail.py"), done_paths=(),
            root="/build", max_file_bytes=4, salvaged_partial=True,
            safe_relpath=lambda path, root: (
                (_ for _ in ()).throw(ValueError("escape"))
                if path.startswith("../") else path),
        )

        self.assertEqual(records, before)
        self.assertEqual(accepted, (("a.py", "A"),))
        self.assertEqual(failures, (
            ("../escape.py", "unsafe path: escape"),
            ("large.py", "file exceeds --max-file-bytes (4)"),
        ))
        self.assertEqual(dropped, ("extra.py",))

    def test_generation_batches_are_model_budgeted_and_do_not_mutate_plan(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        plan = [
            {"path": "done.py", "est_lines": 10},
            {"path": "a.py", "est_lines": 100},
            {"path": "b.py", "est_lines": 100},
        ]
        before = copy.deepcopy(plan)

        batches, max_calls = core.generation_batches(
            plan, done_paths=("done.py",), max_tokens=4000,
            chars_per_token=4.0)

        self.assertEqual(plan, before)
        self.assertEqual(
            tuple(tuple(item["path"] for item in batch) for batch in batches),
            (("a.py",), ("b.py",)),
        )
        self.assertEqual(max_calls, 10)

    def test_generation_prompt_compacts_context_before_refusing_a_file(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        plan = ({"path": "a.py", "purpose": "implementation"},)
        prompt = core.generation_prompt(
            task="build", batch=plan, plan=plan,
            done_paths=("old.py",), context="x" * 5000,
            system_chars=100, single_shot_chars=2600,
            recovery_paths=())

        self.assertIsNotNone(prompt)
        self.assertLessEqual(100 + 2000 + len(prompt), 2600)
        self.assertIn("a.py", prompt)
        self.assertNotIn("old.py", prompt)
        self.assertIsNone(core.generation_prompt(
            task="x" * 1000, batch=plan, plan=plan, done_paths=(), context="",
            system_chars=100, single_shot_chars=2200,
            recovery_paths=()))

    def test_generation_recovery_splits_and_marks_output_starved_files(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        batch = ({"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"})
        retries, attempts, recovery, failures = core.generation_recovery(
            batch, done_paths=("a.py",), failed_paths=(),
            response_meta={"finish_reason": "length"}, attempts={},
            recovery_paths=())

        self.assertEqual(
            tuple(tuple(item["path"] for item in part) for part in retries),
            (("b.py",), ("c.py",)),
        )
        self.assertEqual(attempts, ())
        self.assertEqual(recovery, ("b.py", "c.py"))
        self.assertEqual(failures, ())

    def test_generation_recovery_retries_one_single_file_then_fails(self):
        core = importlib.import_module("ambient_codex.build_workflow")
        batch = ({"path": "a.py"},)
        first = core.generation_recovery(
            batch, done_paths=(), failed_paths=(), response_meta={},
            attempts={}, recovery_paths=())
        second = core.generation_recovery(
            batch, done_paths=(), failed_paths=(), response_meta={},
            attempts=dict(first[1]), recovery_paths=())

        self.assertEqual(first[0], (batch,))
        self.assertEqual(first[1], (("a.py", 1),))
        self.assertEqual(second[0], ())
        self.assertEqual(second[1], (("a.py", 2),))
        self.assertEqual(second[3][0][0], "a.py")
        self.assertIn("after 2 attempts", second[3][0][1])
