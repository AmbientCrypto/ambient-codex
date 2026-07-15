"""Phase 4 contracts for audit preparation and cache identity."""

import importlib
import unittest


class AuditPreparationTests(unittest.TestCase):
    def test_prose_parser_is_a_dedicated_dependency_free_module(self):
        prose = importlib.import_module("ambient_codex.audit_prose")
        payload = prose.parse_prose_findings(
            "HIGH — app.py:7 — missing validation\n"
            "Scenario: user input reaches the sink\n"
            "Fix: validate input\n"
            "Verdict: FIX FIRST",
            verdict=lambda _findings, _partial: "NEEDS WORK",
        )
        self.assertEqual(payload["verdict"], "FIX FIRST")
        self.assertEqual(payload["findings"][0]["file"], "app.py")
        self.assertTrue(prose.text_has_unparsed_finding(
            "Severity: HIGH\nFile: app.py\nLine: 7\nDefect: missing validation"))

    def test_module_owns_audit_prep_and_single_shot_key(self):
        core = importlib.import_module("ambient_codex.audit_core")
        self.assertEqual(core.__all__, (
            "extract_json", "dedupe_findings", "verdict_from", "prepare_sample",
            "single_shot_key", "reduce_findings", "cross_file_suspects", "parse_audit_object",
            "select_cross_file_inputs", "merge_cross_file_findings", "normalize_findings",
            "effective_verdict",
        ))

    def test_json_extraction_accepts_fences_and_marks_safe_repairs(self):
        core = importlib.import_module("ambient_codex.audit_core")
        self.assertEqual(core.extract_json("```json\n{\"findings\": []}\n```"),
                         {"findings": []})
        repaired = core.extract_json('{"findings": []')
        self.assertTrue(repaired["_repaired"])

    def test_dedupe_keeps_higher_severity_and_richer_scenario(self):
        core = importlib.import_module("ambient_codex.audit_core")
        findings = core.dedupe_findings([
            {"severity": "LOW", "file": "src/a.py", "line": 10,
             "title": "missing validation", "scenario": "short"},
            {"severity": "HIGH", "file": "src/a.py", "line": 11,
             "title": "missing validation here", "scenario": "much richer scenario"},
        ])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "HIGH")
        self.assertEqual(findings[0]["scenario"], "much richer scenario")
        self.assertEqual(core.verdict_from(findings, False), "FIX FIRST")

    def test_single_shot_key_uses_a_complete_file_block_and_sample_salt(self):
        core = importlib.import_module("ambient_codex.audit_core")
        calls = []

        def key(*args, **kwargs):
            calls.append((args, kwargs))
            return "key"

        result = core.single_shot_key(
            "model", "system", [("a.py", "x")],
            type("Spec", (), {"max_tokens": 7, "temperature": 0.1,
                                "response_format": None, "_cache_salt": "lane"})(),
            files_block=lambda files: "BLOCK",
            cache_key=key,
        )
        self.assertEqual(result, "key")
        self.assertEqual(calls, [(("model", "system", "BLOCK", 7, 0.1, None),
                                  {"salt": "lane"})])

    def test_reducer_marks_unparseable_or_repaired_chunks_as_incomplete(self):
        core = importlib.import_module("ambient_codex.audit_core")
        values = iter((
            {"findings": [{"severity": "LOW"}]},
            {"findings": [], "_repaired": True},
            None,
        ))
        payload = core.reduce_findings(
            ["one", "two", "three"],
            parse=lambda _: next(values),
            dedupe=lambda findings: findings,
            verdict=lambda findings, partial: "SHIP",
        )
        self.assertEqual(payload["verdict"], "NEEDS WORK")
        self.assertEqual(payload["_repaired_chunks"], 1)
        self.assertEqual(payload["_unparsed_chunks"], 1)

    def test_cross_file_suspects_is_bounded_and_preserves_path_order(self):
        core = importlib.import_module("ambient_codex.audit_core")
        payload = '{"findings":[{"file":"app/a.py","line":1,' \
                  '"title":"app/a.py calls app/b.py"}]}'
        suspects = core.cross_file_suspects(
            payload, ["app/a.py", "app/b.py", "app/c.py"], cap=1)
        self.assertEqual(suspects, ["app/a.py"])

    def test_audit_object_parser_uses_prose_only_when_json_is_insufficient(self):
        core = importlib.import_module("ambient_codex.audit_core")
        self.assertEqual(core.parse_audit_object(
            '{"findings": [{"severity": "LOW"}]}',
            parse_prose=lambda _: None, has_unparsed=lambda _: False),
            {"findings": [{"severity": "LOW"}]})

    def test_cross_file_input_selection_clips_in_order(self):
        core = importlib.import_module("ambient_codex.audit_core")
        picked, used = core.select_cross_file_inputs(
            ["a.py", "b.py"], {"a.py": "a" * 700, "b.py": "b" * 700}, 1_000)
        self.assertEqual(picked, [("a.py", "a" * 700)])
        self.assertEqual(used, 700)

    def test_cross_file_merge_preserves_incomplete_coverage(self):
        core = importlib.import_module("ambient_codex.audit_core")
        merged = core.merge_cross_file_findings(
            {"findings": [{"severity": "LOW"}], "_unparsed_chunks": 1},
            {"findings": [{"severity": "HIGH"}]}, False,
            dedupe=lambda values: values, verdict=lambda *_: "SHIP",
            as_pos_int=lambda value, default: int(value or default))
        self.assertEqual(merged["verdict"], "NEEDS WORK")
        self.assertEqual(merged["_unparsed_chunks"], 1)

    def test_render_normalization_is_immutable_and_verdict_never_fakes_ship(self):
        core = importlib.import_module("ambient_codex.audit_core")
        source = [{"file": "app.py:9", "severity": "HIGH"}, "bad"]
        normalized = core.normalize_findings(source)
        self.assertEqual(source[0]["file"], "app.py:9")
        self.assertEqual(normalized[0]["file"], "app.py")
        self.assertEqual(normalized[0]["line"], 9)
        self.assertEqual(normalized[1], "bad")
        self.assertEqual(
            core.effective_verdict("SHIP", normalized, partial=False,
                                  verdict=lambda *_args: "SHIP"),
            "FIX FIRST")
        self.assertEqual(
            core.effective_verdict("SHIP", [], partial=True,
                                  verdict=lambda *_args: "SHIP"),
            "NEEDS WORK")
