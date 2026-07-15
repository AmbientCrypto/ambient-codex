"""Architecture boundary tests for context, repository coverage, and budgets.

These tests exercise the policy boundaries that ordinary happy-path API tests
do not: a large text file must enter the chunking lane, an unprocessable source
file must become an explicit coverage gap, and the public token ceiling must
not reject a currently advertised frontier output budget.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin" / "ambient"


def load_module():
    loader = importlib.machinery.SourceFileLoader(
        "ambient_architecture_review", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


amb = load_module()


@contextlib.contextmanager
def patched(obj, **attrs):
    missing = object()
    old = {key: getattr(obj, key, missing) for key in attrs}
    for key, value in attrs.items():
        setattr(obj, key, value)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is missing:
                delattr(obj, key)
            else:
                setattr(obj, key, value)


class RepositoryCoverageTests(unittest.TestCase):
    def test_large_text_file_enters_chunking_lane(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "large.py"
            path.write_bytes(b"x\n" * 500_001)
            files, skipped, _used_git = amb.repo_walk(str(root))

        self.assertEqual([item[0] for item in files], ["large.py"])
        self.assertEqual(skipped["oversize"], 0)

    def test_oversized_source_is_an_explicit_coverage_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "small.py").write_text("x = 1\n", encoding="utf-8")
            (root / "too-large.py").write_bytes(b"x\n" * 80)
            args = type("Args", (), {
                "repo": str(root),
                "allow_cost": True,
                "allow_partial": False,
            })()
            with patched(amb, ABS_MAX_CHARS=100,
                         REPO_FILE_MAX_BYTES=100), \
                    contextlib.redirect_stderr(io.StringIO()):
                labeled, meta = amb.repo_audit_inputs(args, "sk-test")

        labels = {label for label, _text in labeled}
        self.assertIn("REPO COVERAGE NOTE", labels)
        self.assertEqual(meta["omitted_oversize"], 1)
        self.assertTrue(meta["coverage_gap"])


class BuildResumeIdentityTests(unittest.TestCase):
    def _identity(self, **changes):
        values = {
            "task": "build it",
            "model": "model/a",
            "reduce_model": "model/a",
            "context_paths": ["a.py"],
            "raw_context_sha": "abc123",
            "max_files": 8,
            "max_file_bytes": 200_000,
            "max_tokens": 16_384,
            "temperature": 0.1,
        }
        values.update(changes)
        return amb.build_resume_identity(**values)

    def test_generation_parameters_invalidate_resume_state(self):
        original = self._identity()
        for field, value in (
            ("max_tokens", 32_768),
            ("temperature", 0.7),
            ("reduce_model", "model/b"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(original, self._identity(**{field: value}))

    def test_identical_build_inputs_keep_a_stable_identity(self):
        self.assertEqual(self._identity(), self._identity())


class RequestBudgetTests(unittest.TestCase):
    def test_public_budget_allows_current_frontier_output_cap(self):
        self.assertGreaterEqual(amb.MAX_REQUESTED_TOKENS, 262144)

    def test_budget_normalizer_only_clamps_above_safety_ceiling(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(amb.normalize_requested_max_tokens(262144), 262144)
            self.assertEqual(
                amb.normalize_requested_max_tokens(amb.MAX_REQUESTED_TOKENS + 1),
                amb.MAX_REQUESTED_TOKENS,
            )
        self.assertIn("capped", err.getvalue())

    def test_reasoning_cost_estimate_reserves_the_full_output_budget(self):
        catalog = [{
            "id": "reasoner",
            "context_length": 262_144,
            "max_output_length": 131_072,
            "supported_features": ["reasoning"],
            "pricing": {"input": 1.0, "output": 2.0},
        }]

        expected, bound, assumed = amb.estimate_cost(
            catalog, "reasoner", 100_000, 10, 65_536
        )

        self.assertFalse(assumed)
        self.assertEqual(expected, bound)

    def test_non_reasoning_cost_estimate_keeps_the_answer_reserve(self):
        catalog = [{
            "id": "direct",
            "context_length": 262_144,
            "max_output_length": 131_072,
            "supported_features": [],
            "pricing": {"input": 1.0, "output": 2.0},
        }]

        expected, bound, assumed = amb.estimate_cost(
            catalog, "direct", 100_000, 10, 65_536
        )

        self.assertFalse(assumed)
        self.assertLess(expected, bound)

    def test_unknown_model_cost_estimate_is_conservative(self):
        expected, bound, assumed = amb.estimate_cost(
            [], "unknown", 100_000, 10, 65_536
        )

        self.assertTrue(assumed)
        self.assertEqual(expected, bound)

    def test_reasoning_auto_budget_honors_global_ceiling(self):
        catalog = [{
            "id": "large-reasoner",
            "context_length": 1_000_000,
            "max_output_length": 262_144,
            "supported_features": ["reasoning"],
        }]

        profile = amb.model_profile(catalog, "large-reasoner")

        self.assertLessEqual(
            profile.output_budget, amb.MAX_AUTO_BUDGET_TOKENS
        )


if __name__ == "__main__":
    unittest.main()
