"""Phase 3G contracts for map-reduce planning helpers."""

import importlib
import unittest


class MapReducePlanningTests(unittest.TestCase):
    def test_module_owns_prompt_and_budget_grouping(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        self.assertEqual(
            core.__all__,
            ("files_block", "chunk_ranges", "code_map_budget", "map_note", "group_for_budget",
             "resolve_parallel", "reduce_response_format", "coverage_gap",
             "hierarchical_reduce", "partial_reason", "collect_fanout", "run_chunk",
             "synthesize_parts"),
        )

    def test_input_helpers_preserve_file_boundaries_and_coverage_ranges(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        block = core.files_block([("a.py", "x = 1")])
        self.assertEqual(block, "===== FILE: a.py =====\nx = 1")
        self.assertEqual(core.chunk_ranges("===== a.py [lines 1-2] =====\nx"),
                         ["a.py [lines 1-2]"])
        self.assertEqual(core.code_map_budget(10_000, 4_000, 40_000), 1_000)

    def test_grouping_is_ordered_and_never_overflows_a_joinable_group(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        groups = core.group_for_budget(["aaa", "bbbb", "cc", "dddd"], 7)
        self.assertEqual(groups, [["aaa", "bbbb"], ["cc", "dddd"]])

    def test_map_note_uses_the_supplied_collision_safe_index_marker(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        note = core.map_note("inspect", "MAP", 3, "<index>")
        self.assertIn("<index> of 3", note)
        self.assertIn("MAP", note)

    def test_parallel_and_reduce_format_policies_are_core_owned(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        self.assertEqual(core.resolve_parallel(["1000", "2"], 4), 16)
        self.assertEqual(core.resolve_parallel(["bad", "2"], 4), 2)
        profile = type("Profile", (), {"features": []})()
        self.assertIsNone(core.reduce_response_format(
            {"type": "json_object"}, profile,
            response_format_for=lambda *_: {"unexpected": True}))

    def test_coverage_and_partial_reason_preserve_incomplete_work(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        gap = core.coverage_gap(["chunk 2: timeout"], [3])
        self.assertIn("1 chunk(s) FAILED", gap)
        self.assertIn("chunk(s) [3] were TRUNCATED", gap)
        partial, reason = core.partial_reason(
            errors=["chunk 2: timeout"], truncated=[3], synth_failed=True,
            missed_ranges=["a.py [1-2]", "a.py [1-2]"], chunk_count=3)
        self.assertTrue(partial)
        self.assertIn("1 of 3 chunks failed", reason)
        self.assertIn("synthesis was incomplete", reason)
        self.assertEqual(reason.count("a.py [1-2]"), 1)

    def test_hierarchical_reduce_groups_orderedly_and_preserves_merge_failure(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        calls = []

        def merge(parts):
            calls.append(list(parts))
            return "[" + "+".join(parts) + "]", len(calls) != 1

        final, failed = core.hierarchical_reduce(
            ["aaa", "bbb", "ccc"], effective_budget=6, merge=merge)
        self.assertEqual(calls, [["aaa", "bbb"], ["[aaa+bbb]", "ccc"]])
        self.assertEqual(final, "[[aaa+bbb]+ccc]")
        self.assertTrue(failed)

    def test_fanout_collector_orders_results_and_reports_coverage_gaps(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        import concurrent.futures

        class Event:
            def __init__(self):
                self.cancelled = False

            def set(self):
                self.cancelled = True

        def work(index):
            if index == 1:
                raise RuntimeError("broken")
            return f"item-{index}", False

        results, errors, missed = core.collect_fanout(
            ["one", "two", "three"], work=work, width=2,
            cancel_event=Event(), chunk_ranges=lambda value: [f"range:{value}"],
            executor=concurrent.futures.ThreadPoolExecutor,
            as_completed=concurrent.futures.as_completed)
        self.assertEqual(results, [("item-0", False), None, ("item-2", False)])
        self.assertEqual(len(errors), 1)
        self.assertIn("chunk 2 [range:two]: RuntimeError: broken", errors[0])
        self.assertEqual(missed, ["range:two"])

    def test_chunk_worker_uses_cache_and_never_caches_partial_or_fallback(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        spec = type("Spec", (), {"max_tokens": 10, "temperature": 0,
                                   "response_format": None, "_cache_salt": "lane"})()
        event = _Event()
        cached = core.run_chunk(
            0, chunks=["body"], map_note="system <i>", index_marker="<i>",
            model="requested", spec=spec, session=object(), cancel_event=event,
            gate=None, cache_key=lambda *_args, **_kwargs: "key",
            cache_get=lambda *_args: "cached", cache_put=lambda *_args: self.fail("no write"),
            cache_ttl=1, complete=lambda *_args, **_kwargs: self.fail("no call"),
            chat_error=_ChatError, retry_delay=lambda *_args: 0, sleep=lambda _: None,
            use_cache=True)
        self.assertEqual(cached, ("cached", False, True))

        writes, calls = [], []

        def complete(*_args, **_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise _ChatError("rate", "wait")
            return "answer", {}, {"finish_reason": "length", "_served_model": "fallback"}

        output = core.run_chunk(
            0, chunks=["body"], map_note="system <i>", index_marker="<i>",
            model="requested", spec=spec, session=object(), cancel_event=event,
            gate=_NullContext(), cache_key=lambda *_args, **_kwargs: "key",
            cache_get=lambda *_args: None, cache_put=lambda *args: writes.append(args),
            cache_ttl=1, complete=complete, chat_error=_ChatError,
            retry_delay=lambda *_args: 0, sleep=lambda _: None, use_cache=True)
        self.assertEqual(output, ("answer", True, False))
        self.assertEqual(len(calls), 2)
        self.assertEqual(writes, [])

    def test_synthesis_marks_partial_or_preserves_raw_parts_on_recoverable_failure(self):
        core = importlib.import_module("ambient_codex.map_reduce")
        text, complete = core.synthesize_parts(
            ["one", "two"], system="merge", gap=" GAP", model="reduce",
            spec=object(), session=object(),
            complete=lambda *_args, **_kwargs: ("merged", {}, {"finish_reason": "length"}),
            recoverable_errors=(RuntimeError,))
        self.assertEqual(text, "merged")
        self.assertFalse(complete)
        raw, complete = core.synthesize_parts(
            ["one", "two"], system="merge", gap=" GAP", model="reduce",
            spec=object(), session=object(),
            complete=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")),
            recoverable_errors=(RuntimeError,))
        self.assertEqual(raw, "----- PART 1 -----\none\n\n----- PART 2 -----\ntwo")
        self.assertFalse(complete)


class _ChatError(Exception):
    def __init__(self, category, diagnosis):
        super().__init__(diagnosis)
        self.category = category
        self.diagnosis = diagnosis
        self.retry_after = None


class _Event:
    def is_set(self):
        return False


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
