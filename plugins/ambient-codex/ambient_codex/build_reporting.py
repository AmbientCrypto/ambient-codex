"""Final build reconciliation and user-facing result emission."""

import dataclasses
import sys


@dataclasses.dataclass(frozen=True)
class BuildReport:
    args: object
    task: str
    model: str
    api_key: str
    root: str
    state: object
    done: object
    failed: tuple
    dropped: tuple
    actions: object
    advisory: tuple
    headless: bool
    backup_stamp: str
    target_file_bytes: int


def _merged_actions(current, applied):
    merged = dict(current)
    for relative, action in applied:
        if merged.get(relative) in ("create", "overwrite") \
                and action == "unchanged":
            continue
        merged[relative] = action
    return merged


def emit_build_report(report, deps):
    """Reconcile approved records and emit one complete build result."""
    args = report.args
    done = dict(report.done)
    failed = [dict(item) for item in report.failed]
    actions = dict(report.actions)
    partial = bool(failed)
    total_bytes = sum(
        len(record["content"].encode()) for record in done.values())
    written = False
    if args.apply and done:
        proceed = True
        if not report.headless and not args.yes:
            try:
                answer = input(
                    f"Write {len(done)} file(s) ({total_bytes:,} bytes) "
                    f"into {report.root}? [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            proceed = answer in ("y", "yes")
        if proceed:
            applied, apply_failures = deps._build_apply.apply_records(
                done,
                report.root,
                force=args.force,
                backup_stamp=report.backup_stamp,
                within_root=deps._within_root,
            )
            actions = _merged_actions(actions, applied)
            failed.extend(
                {"path": path, "reason": reason}
                for path, reason in apply_failures
            )
            if apply_failures or any(
                    action == "skip-exists"
                    for action in actions.values()):
                partial = True
            written = any(
                action in ("create", "overwrite", "unchanged")
                for action in actions.values()
            )
        else:
            print(
                "ambient: not written (declined) — the manifest below is "
                "cached in .ambient-build.json; re-run with --apply --yes "
                "to write without asking",
                file=sys.stderr,
            )
    files_out = [
        {
            "path": relative,
            "bytes": len(record["content"].encode()),
            "sha256": record["sha256"],
            "action": actions.get(
                relative, "generated" if not written else "create"),
        }
        for relative, record in sorted(done.items())
    ]
    reason = None
    if failed:
        reason = f"{len(failed)} file(s) failed: " + "; ".join(
            f"{item['path']} ({item['reason']})" for item in failed[:10])
    elif partial:
        skipped = [
            relative for relative, action in actions.items()
            if action == "skip-exists"
        ]
        if skipped:
            reason = (
                f"{len(skipped)} file(s) already exist and were left "
                "unchanged (pass --force to overwrite): "
                + ", ".join(skipped[:10])
            )
    if args.json:
        deps.emit_json(
            "build",
            model=report.model,
            api_key=report.api_key,
            partial=partial,
            reason=reason,
            allow_partial=getattr(args, "allow_partial", False),
            extra={
                "root": report.root,
                "files": files_out,
                "failed": failed,
                "dropped": list(report.dropped),
                "advisory_steps": list(report.advisory),
                "notes": report.state.get("notes", ""),
                "project_brief": report.state.get("project_brief", ""),
                "metrics": report.state.get("metrics", {}),
                "target_file_bytes": report.state.get(
                    "target_file_bytes", report.target_file_bytes),
                "written": written,
                "total_bytes": total_bytes,
            },
        )
        return
    print(f"\nBuild result for: {report.task}")
    if written:
        target_state = "WRITTEN"
    elif args.apply:
        target_state = (
            f"nothing written — {len(failed)} file(s) failed"
            if failed else "nothing written"
        )
    else:
        target_state = "manifest only — re-run with --apply to write"
    print(f"  target: {report.root} ({target_state})")
    for item in files_out:
        print(
            f"  {item['action']:>11}  {item['path']:44} "
            f"{item['bytes']:>8,} bytes"
        )
    for item in failed:
        print(deps.redact(
            f"       FAILED  {item['path']:44} {item['reason']}",
            report.api_key,
        ))
    for dropped in report.dropped:
        print(
            f"      DROPPED  {dropped:44} "
            "not in the approved plan (not written)"
        )
    if report.state.get("notes"):
        print(f"\n{deps.redact(report.state['notes'], report.api_key)}")
    if report.advisory:
        print("\nSuggested next steps (NOT executed — review first):")
        for step in report.advisory:
            print(deps.redact(f"  {step}", report.api_key))
    if partial and not getattr(args, "allow_partial", False):
        sys.exit(deps.EXIT_PARTIAL)


def emit_plan_only(*, args, model, api_key, root, task, state, plan,
                   advisory, target_file_bytes, deps):
    """Emit a cached planning contract without entering generation."""
    failures = state.get("plan_failures") or state.get("failed") or []
    if args.json:
        reason = (
            f"{len(failures)} planned path(s) rejected: "
            + "; ".join(
                f"{item['path']} ({item['reason']})"
                for item in failures[:10]
            )
            if failures else None
        )
        deps.emit_json(
            "build", model=model, api_key=api_key,
            partial=bool(failures), reason=reason,
            allow_partial=getattr(args, "allow_partial", False),
            extra={
                "root": root,
                "plan": plan,
                "project_brief": state.get("project_brief", ""),
                "notes": state.get("notes", ""),
                "advisory_steps": advisory,
                "failed": failures,
                "target_file_bytes": state.get(
                    "target_file_bytes", target_file_bytes),
                "written": False,
            },
        )
        return
    print(f"Build plan for: {task}\n")
    for item in plan:
        print(
            f"  {item['path']:44} ~{item.get('est_lines', '?')} lines  "
            f"{item.get('purpose', '')}"
        )
    if state.get("notes"):
        print(f"\n{state['notes']}")
    print(
        "\nGenerate with the same command minus --plan-only "
        "(the plan is cached — planning is not re-billed)."
    )


__all__ = ("BuildReport", "emit_build_report", "emit_plan_only")
