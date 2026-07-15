"""Phase 2D2B contracts for bounded usage-ledger record reads."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = ("read_records", "filter_recent", "summarize_records",
               "usage_payload", "usage_lines")


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2d2b", str(BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class UsageReportOwnershipTests(unittest.TestCase):
    def test_module_owns_exact_exports(self):
        report = importlib.import_module("ambient_codex.usage_report")
        self.assertEqual(report.__all__, MOVED_NAMES)

    def test_import_is_side_effect_free_in_fresh_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.usage_report"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class ReadRecordsTests(unittest.TestCase):
    def test_keeps_dicts_and_counts_blank_bad_and_nonobject(self):
        report = importlib.import_module("ambient_codex.usage_report")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text(
                '{"model":"a","in":1}\n'   # valid dict
                "\n"                          # blank -> skipped, not counted
                "   \n"                       # whitespace -> skipped
                "not json\n"                  # unparseable -> bad
                "42\n"                         # non-object -> bad
                '"x"\n'                        # non-object -> bad
                '{"model":"b"}\n',            # valid dict
                encoding="utf-8",
            )
            records, bad = report.read_records(str(ledger))
            self.assertEqual(records, [{"model": "a", "in": 1}, {"model": "b"}])
            self.assertEqual(bad, 3)

    def test_missing_file_raises_filenotfound(self):
        report = importlib.import_module("ambient_codex.usage_report")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                report.read_records(str(Path(td) / "nope.jsonl"))

    def test_unreadable_kind_raises_oserror(self):
        report = importlib.import_module("ambient_codex.usage_report")
        with tempfile.TemporaryDirectory() as td:
            # a directory in place of the ledger -> OSError family on open
            with self.assertRaises(OSError):
                report.read_records(td)

    def test_empty_ledger_is_empty_with_zero_bad(self):
        report = importlib.import_module("ambient_codex.usage_report")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            ledger.write_text("", encoding="utf-8")
            self.assertEqual(report.read_records(str(ledger)), ([], 0))

    def test_oversize_integer_line_counted_corrupt_not_crashed(self):
        report = importlib.import_module("ambient_codex.usage_report")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "usage.jsonl"
            # A 5000-digit integer line raises a plain ValueError on 3.11+ and
            # parses to a non-dict int on older Python -- counted bad either way,
            # never crashing the reader (Codex 2D2B finding 1).
            ledger.write_text("9" * 5000 + "\n" + '{"model":"ok"}\n',
                              encoding="utf-8")
            records, bad = report.read_records(str(ledger))
            self.assertEqual(records, [{"model": "ok"}])
            self.assertEqual(bad, 1)


class FilterRecentTests(unittest.TestCase):
    def test_keeps_records_at_or_after_cutoff(self):
        report = importlib.import_module("ambient_codex.usage_report")
        records = [{"ts": 100}, {"ts": 200}, {"ts": 50}, {"ts": "bad"}]
        recent = report.filter_recent(
            records, 100, lambda r: r["ts"] if isinstance(r["ts"], int) else 0)
        self.assertEqual(recent, [{"ts": 100}, {"ts": 200}])

    def test_usage_summary_is_immutable_and_exposes_only_relative_savings(self):
        report = importlib.import_module("ambient_codex.usage_report")
        records = [{"model": "example/model", "in": 1_000_000, "out": 0,
                    "cost": 1.0, "ref": [3.0, 4.0], "est": True}]
        summary = report.summarize_records(
            records, pricing={}, default_reference=(3.0, 4.0),
            positive_int=lambda value, default: int(value) if isinstance(value, int) else default,
        )
        self.assertEqual(records[0]["cost"], 1.0)
        self.assertEqual(summary["rows"], [{
            "calls": 1, "in": 1_000_000, "out": 0,
            "model": "example/model", "est_records": 1,
            "cost_partial": False, "saved_pct": 66,
        }])
        self.assertTrue(summary["all_priced"])
        self.assertEqual(summary["est_records"], 1)

    def test_renderers_keep_money_private_and_savings_opt_in(self):
        report = importlib.import_module("ambient_codex.usage_report")
        summary = {
            "rows": [{"model": "m", "calls": 1, "in": 2, "out": 3,
                      "est_records": 0, "cost_partial": False,
                      "saved_pct": 50}],
            "raw_rows": [(1.0, 2.0, 1.0, 50)],
            "grand_frontier": 2.0, "grand_saved": 1.0,
            "all_priced": True, "approx_ref_records": 0,
            "est_records": 0,
        }
        hidden = report.usage_payload(
            summary, days=7, show_savings=False, note="CLI only")
        shown = report.usage_payload(
            summary, days=7, show_savings=True, note="CLI only")

        self.assertIsNone(hidden["saved_pct"])
        self.assertIsNone(hidden["models"][0]["saved_pct"])
        self.assertEqual(shown["saved_pct"], 50)
        self.assertFalse({"cost", "price", "frontier_cost"} & shown.keys())
        self.assertFalse({"cost", "price", "frontier_cost"}
                         & shown["models"][0].keys())
        self.assertIn("Overall usage summary", "\n".join(report.usage_lines(
            summary, days=7, show_savings=False, note="CLI only")))


class UsageReportFacadeTests(unittest.TestCase):
    def test_cmd_usage_returns_empty_json_for_missing_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._usage_report, "read_records",
                    side_effect=FileNotFoundError()):
                with mock.patch("sys.stdout") as output:
                    facade.cmd_usage(types.SimpleNamespace(days=7, json=True))
            payload = "".join(
                str(call.args[0]) for call in output.write.call_args_list if call.args)
            self.assertIn('"empty": true', payload)
            self.assertIn('"models": []', payload)

    def test_cmd_usage_reports_bad_lines_from_reader(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            with mock.patch.object(
                    facade._usage_report, "read_records",
                    return_value=([], 4)):
                # No records in the selected window is a successful empty report.
                with mock.patch("sys.stderr") as err, mock.patch("sys.stdout") as output:
                    facade.cmd_usage(types.SimpleNamespace(days=7, json=True))
            printed = "".join(
                str(c.args[0]) for c in err.write.call_args_list if c.args)
            self.assertIn("skipped 4 corrupt", printed)
            payload = "".join(
                str(call.args[0]) for call in output.write.call_args_list if call.args)
            self.assertIn('"empty": true', payload)

    def test_cmd_usage_maps_generic_oserror_to_cannot_read(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            facade.USAGE_PATH = "/patched/usage.jsonl"
            with mock.patch.object(facade._usage_report, "read_records",
                                   side_effect=OSError("boom")):
                with self.assertRaises(SystemExit) as ctx:
                    facade.cmd_usage(types.SimpleNamespace(days=7))
            self.assertIn("cannot read /patched/usage.jsonl: boom",
                          str(ctx.exception))

    def test_cmd_usage_wires_reader_and_recency_filter(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            facade.USAGE_PATH = "/patched/usage.jsonl"
            recs = [{"ts": 10 ** 9, "model": "m", "in": 1, "out": 1}]
            with mock.patch.object(facade._usage_report, "read_records",
                                   return_value=(recs, 0)) as reader, \
                    mock.patch.object(facade._usage_report, "filter_recent",
                                      return_value=[]) as recent:
                with mock.patch("sys.stdout"):
                    facade.cmd_usage(types.SimpleNamespace(days=7, json=True))
            reader.assert_called_once_with("/patched/usage.jsonl")
            self.assertEqual(recent.call_count, 1)
            args, _ = recent.call_args
            self.assertEqual(args[0], recs)
            ts_of = args[2]
            self.assertTrue(callable(ts_of))
            self.assertEqual(ts_of({"ts": 5}), 5)
            self.assertEqual(ts_of({"ts": "x"}), 0)


if __name__ == "__main__":
    unittest.main()
