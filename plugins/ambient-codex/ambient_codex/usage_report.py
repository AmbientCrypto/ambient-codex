"""Bounded usage-ledger reads and aggregation for the local usage summary.

The ledger is a JSON-lines file of metering records written by the persistence
layer (``ambient_codex.usage_store``). Reading it is separated from pricing and
report math: this module only parses the file into well-formed records and
filters by recency, so the caller keeps ownership of catalog pricing, reference
math, and display. Reads raise the OS error family (missing file, unreadable
path) so the caller can map each to its own user-facing message.
"""

import json


def read_records(usage_path):
    """Return ``(records, bad)`` from the JSON-lines ledger: every well-formed
    object line as a dict, and a count of non-blank lines that were unparseable
    or not a JSON object. Blank/whitespace lines are skipped without counting.
    The open is not guarded here; a missing or unreadable ledger raises the OS
    error family to the caller."""
    records = []
    bad = 0
    with open(usage_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # Deliberately broader than json.JSONDecodeError: json.loads can
                # also raise a plain ValueError (e.g. an integer line past the
                # int-string digit limit on 3.11+). The old inline reader crashed
                # on that; counting it corrupt is intentional, tested hardening.
                bad += 1
                continue
            if not isinstance(record, dict):
                bad += 1
                continue
            records.append(record)
    return records, bad


def filter_recent(records, cutoff, ts_of):
    """Return the records whose timestamp (via ``ts_of``) is at or after
    ``cutoff``. ``ts_of`` maps a record to a comparable number, letting the
    caller own timestamp coercion."""
    return [record for record in records if ts_of(record) >= cutoff]


def _record_totals(records, pricing, default_reference, positive_int):
    """Aggregate records without mutating provider-owned ledger objects."""
    totals, approximate_reference = {}, 0
    for record in records:
        model = str(record.get("model", "?"))
        total = totals.setdefault(model, {"calls": 0, "in": 0, "out": 0,
                                          "cost": 0.0, "unpriced": 0,
                                          "frontier": 0.0, "est": 0})
        total["calls"] += 1
        total["est"] += 1 if record.get("est") else 0
        incoming = positive_int(record.get("in"), 0)
        outgoing = positive_int(record.get("out"), 0)
        total["in"] += incoming
        total["out"] += outgoing
        cost = record.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            total["cost"] += float(cost)
        elif model in pricing:
            total["cost"] += (incoming * pricing[model][0]
                              + outgoing * pricing[model][1]) / 1e6
        else:
            total["unpriced"] += 1
        reference = record.get("ref")
        if (isinstance(reference, list) and len(reference) == 2
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and 0 < value < float("inf") for value in reference)):
            reference_in, reference_out = float(reference[0]), float(reference[1])
        else:
            reference_in, reference_out = default_reference
            approximate_reference += 1
        total["frontier"] += (incoming * reference_in + outgoing * reference_out) / 1e6
    return totals, approximate_reference


def _summary_rows(totals):
    """Build public rows and private comparison figures from aggregate totals."""
    rows, raw_rows = [], []
    grand, grand_frontier, priced_cost = 0.0, 0.0, 0.0
    all_priced = True
    for model, total in sorted(totals.items()):
        base = {"calls": total["calls"], "in": total["in"], "out": total["out"],
                "model": model, "est_records": total["est"]}
        grand += total["cost"]
        if total["unpriced"]:
            all_priced = False
            rows.append({**base, "cost_partial": True, "saved_pct": None})
            raw_rows.append(("partial", total["cost"]) if total["cost"] > 0 else None)
            continue
        priced_cost += total["cost"]
        grand_frontier += total["frontier"]
        saved = total["frontier"] - total["cost"]
        percentage = (int(saved / total["frontier"] * 100)
                      if total["frontier"] > 0 and saved > 0 else None)
        rows.append({**base, "cost_partial": False, "saved_pct": percentage})
        raw_rows.append((total["cost"], total["frontier"], saved, percentage))
    return rows, raw_rows, grand_frontier, priced_cost, all_priced


def summarize_records(records, *, pricing, default_reference, positive_int):
    """Return immutable public rows plus private relative-comparison totals."""
    totals, approximate_reference = _record_totals(
        records, pricing, default_reference, positive_int)
    rows, raw_rows, frontier, priced_cost, all_priced = _summary_rows(totals)
    return {
        "rows": rows, "raw_rows": raw_rows, "grand": sum(
            total["cost"] for total in totals.values()),
        "grand_frontier": frontier, "priced_cost": priced_cost,
        "grand_saved": frontier - priced_cost, "all_priced": all_priced,
        "approx_ref_records": approximate_reference,
        "est_records": sum(total["est"] for total in totals.values()),
    }


def usage_payload(summary, *, days, show_savings, note):
    """Return the public token/relative-savings JSON payload."""
    frontier = summary["grand_frontier"]
    saved = summary["grand_saved"]
    overall = (int(saved / frontier * 100)
               if show_savings and frontier > 0 and saved > 0 else None)
    models = (summary["rows"] if show_savings else
              [{**row, "saved_pct": None} for row in summary["rows"]])
    return {
        "schema_version": 1,
        "days": days,
        "models": models,
        "empty": False,
        "all_priced": summary["all_priced"],
        "saved_pct": overall,
        "approx_ref_records": summary["approx_ref_records"],
        "est_records": summary["est_records"],
        "unmetered_lanes": ["agent"],
        "note": note,
    }


def _row_tail(row, raw, show_savings):
    if not show_savings:
        tail = ""
    elif raw is None:
        tail = "est n/a"
    elif raw[0] == "partial":
        tail = "(partial — some records unpriced)"
    else:
        saved, percentage = raw[2], raw[3]
        tail = (f"~{percentage}% cheaper than frontier" if percentage is not None
                else "costlier than frontier" if saved < 0 else "")
    if row["est_records"]:
        tail += f"  [{row['est_records']} est.]"
    return tail


def usage_lines(summary, *, days, show_savings, note):
    """Return the human usage report as immutable display lines."""
    lines = [f"Ambient usage, last {days} days:"]
    for row, raw in zip(summary["rows"], summary["raw_rows"]):
        tail = _row_tail(row, raw, show_savings)
        lines.append(
            f"  {row['model']:42} {row['calls']:4} calls  "
            f"in={row['in']:>9,}  out={row['out']:>9,}  {tail}")
    frontier, saved = summary["grand_frontier"], summary["grand_saved"]
    if show_savings and frontier > 0 and saved > 0:
        total = (f"Overall: ~{int(saved / frontier * 100)}% cheaper than a "
                 "frontier-equivalent model")
    elif show_savings and frontier > 0 and saved < 0:
        total = "Overall: costlier than a frontier-equivalent model"
    else:
        total = "Overall usage summary"
    if show_savings and not summary["all_priced"]:
        total += " (some models unpriced)"
    lines.append(total)
    if summary["est_records"]:
        lines.append(
            f"note: {summary['est_records']} estimated record(s) — token counts "
            "approximated locally (stream sent no usage object), not exact.")
    if show_savings and summary["approx_ref_records"]:
        lines.append(
            f"note: {summary['approx_ref_records']} record(s) use the current "
            "comparison baseline (approx).")
    lines.append(f"note: {note}.")
    return tuple(lines)


__all__ = ("read_records", "filter_recent", "summarize_records",
           "usage_payload", "usage_lines")
