"""Phase 5 contracts for extracted workflow parser construction."""

import argparse
import importlib
import unittest


class WorkflowParserTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.cli_parser")

    def test_common_flags_preserve_progress_tristate_and_model_defaults(self):
        parser = argparse.ArgumentParser()
        self.core.add_common_flags(
            parser, default_timeout_s=300, max_parallel_chunks=4)

        defaults = parser.parse_args([])
        self.assertIsNone(defaults.model)
        self.assertEqual(defaults.timeout, 300)
        self.assertFalse(hasattr(defaults, "progress"))
        self.assertTrue(parser.parse_args(["--progress"]).progress)
        self.assertFalse(parser.parse_args(["--no-progress"]).progress)

    def test_large_workflow_parsers_keep_critical_flags(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        common = lambda target: self.core.add_common_flags(  # noqa: E731
            target, default_timeout_s=300, max_parallel_chunks=4)
        best = lambda target: self.core.add_best_of_flag(  # noqa: E731
            target, best_of_max=8)
        self.core.configure_audit(
            sub, add_common=common, add_best_of=best)
        self.core.configure_map(sub, add_common=common)
        self.core.configure_build(sub, add_common=common)

        audit = parser.parse_args(["audit", "--repo", ".", "--no-deep"])
        self.assertEqual(audit.repo, ".")
        self.assertFalse(audit.deep)
        build = parser.parse_args([
            "build", "game", "--dir", "out", "--max-files", "7",
            "--no-progress",
        ])
        self.assertEqual(build.max_files, 7)
        self.assertFalse(build.progress)
        mapped = parser.parse_args(["map", "summarize", "a.py", "--json"])
        self.assertTrue(mapped.json)

    def test_control_and_small_workflow_parsers_are_module_owned(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        common = lambda target: self.core.add_common_flags(  # noqa: E731
            target, default_timeout_s=300, max_parallel_chunks=4)
        best = lambda target: self.core.add_best_of_flag(  # noqa: E731
            target, best_of_max=8)
        self.core.configure_control(
            sub, parser_class=argparse.ArgumentParser)
        self.core.configure_ask(
            sub, add_common=common, add_best_of=best)
        self.core.configure_code(
            sub, add_common=common, add_best_of=best)
        self.core.configure_chat(sub, add_common=common)

        mode = parser.parse_args(["control", "mode", "takeover"])
        self.assertEqual(mode.state, "takeover")
        ask = parser.parse_args(["ask", "hello", "--best-of", "2"])
        self.assertEqual(ask.best_of, 2)
        code = parser.parse_args(["code", "build", "--json"])
        self.assertTrue(code.json)
        chat = parser.parse_args(["chat", "--no-progress"])
        self.assertFalse(chat.progress)


if __name__ == "__main__":
    unittest.main()
