"""Injected usage, cache, and uninstall command orchestration."""

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageDependencies:
    usage_path: str
    read_config: object
    savings_enabled: object
    report: object
    positive_int: object
    resolve_api_url: object
    fetch_models: object
    model_pricing: object
    resolve_reference_price: object
    network_error: object
    usage_error: object
    now: object


@dataclass(frozen=True)
class CacheDependencies:
    cache_dir: str
    usage_error: object
    now: object


@dataclass(frozen=True)
class UninstallDependencies:
    state_dir: str
    foreign_root: object
    keychain_delete: object
    keychain_read: object
    save_config: object
    command_link: object
    launcher_name: str
    keychain_service: str


def empty_usage_payload(days, note):
    return {
        "schema_version": 1,
        "days": days,
        "models": [],
        "empty": True,
        "all_priced": True,
        "saved_pct": None,
        "approx_ref_records": 0,
        "est_records": 0,
        "unmetered_lanes": ["agent"],
        "note": note,
    }


def print_empty_usage(args, note, message):
    if getattr(args, "json", False):
        print(json.dumps(empty_usage_payload(args.days, note), indent=2))
        return
    print(message)
    print(f"note: {note}.")


def run_usage(args, deps):
    if args.days <= 0:
        return deps.usage_error("--days must be a positive number of days")
    conf = deps.read_config()
    show_savings = deps.savings_enabled(conf)
    note = ("`ambient-codex agent` activity is not included in local usage "
            "totals — these totals cover only ambient CLI calls")
    records = _read_usage_records(args, deps, note)
    if records is None:
        return
    recent = deps.report.filter_recent(
        records, deps.now() - args.days * 86400,
        lambda record: deps.positive_int(record.get("ts"), 0))
    if not recent:
        print_empty_usage(
            args, note, f"No usage in the last {args.days} days.")
        return
    pricing = _usage_pricing(conf, deps)
    summary = deps.report.summarize_records(
        recent, pricing=pricing,
        default_reference=deps.resolve_reference_price(conf),
        positive_int=deps.positive_int)
    _render_usage(args, summary, show_savings, note, deps.report)


def _read_usage_records(args, deps, note):
    try:
        records, bad = deps.report.read_records(deps.usage_path)
    except FileNotFoundError:
        print_empty_usage(args, note, "No usage recorded yet.")
        return None
    except OSError as error:
        raise SystemExit(f"ambient: cannot read {deps.usage_path}: {error}")
    if bad:
        print(f"ambient: skipped {bad} corrupt usage line(s)", file=sys.stderr)
    return records


def _usage_pricing(conf, deps):
    pricing = {}
    try:
        api_url = deps.resolve_api_url(conf)
        for model in deps.fetch_models(api_url, "unused"):
            price = deps.model_pricing([model], model.get("id"))
            if price:
                pricing = {**pricing, model["id"]: price}
    except (deps.network_error, SystemExit):
        pass
    return pricing


def _render_usage(args, summary, show_savings, note, report):
    if getattr(args, "json", False):
        payload = report.usage_payload(
            summary, days=args.days, show_savings=show_savings, note=note)
        print(json.dumps(payload, indent=2))
        return
    for line in report.usage_lines(
            summary, days=args.days, show_savings=show_savings, note=note):
        print(line)


def run_cache(args, deps):
    older_than = getattr(args, "older_than", None)
    if args.action == "clear" and older_than is not None and older_than < 0:
        return deps.usage_error(
            "--older-than must be a non-negative number of days "
            "(a negative value would clear the entire cache)")
    entries = _cache_entries(deps.cache_dir)
    if args.action == "clear":
        _clear_cache(entries, older_than, deps)
        return
    total = sum(_file_size(os.path.join(deps.cache_dir, entry))
                for entry in entries)
    print(f"cache: {len(entries)} entries, {total:,} bytes at {deps.cache_dir}")
    print("clear with: ambient-codex cache clear [--older-than DAYS]")


def _cache_entries(cache_dir):
    try:
        return tuple(entry for entry in os.listdir(cache_dir)
                     if entry.endswith(".json"))
    except OSError:
        return ()


def _file_size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _clear_cache(entries, older_than, deps):
    cutoff = deps.now() - older_than * 86400 if older_than else None
    removed = 0
    for entry in entries:
        path = os.path.join(deps.cache_dir, entry)
        try:
            if cutoff is not None and os.stat(path).st_mtime > cutoff:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    print(f"cache: removed {removed} of {len(entries)} entries "
          f"({deps.cache_dir})")


def run_uninstall(args, deps):
    purge = getattr(args, "purge", False)
    if deps.foreign_root(deps.state_dir) is not None:
        raise SystemExit(
            f"ambient: refusing to uninstall — state root {deps.state_dir} is "
            "inside another Ambient install's tree.")
    if not _confirm_uninstall(args, purge, deps.state_dir):
        return
    _remove_key(deps)
    _remove_launcher(args, deps)
    _remove_state(purge, deps)
    print("\nTo remove the plugin from Codex, run:")
    print("  codex plugin remove ambient-codex@ambient-codex")
    print("Git audit hooks, if you installed any, stay until you run "
          f"`{deps.launcher_name} audit --uninstall-hook <hook>` in each repo.")


def _confirm_uninstall(args, purge, state_dir):
    if getattr(args, "yes", False) or not sys.stdin.isatty():
        return True
    print("This removes Ambient Codex's API key and PATH launcher"
          + (f", and deletes {state_dir}" if purge else "") + ".")
    print("It does NOT touch any other Ambient install.")
    try:
        if input("Continue? [y/N] ").strip().lower() in ("y", "yes"):
            return True
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False
    print("Cancelled.")
    return False


def _remove_key(deps):
    keychain_ok = deps.keychain_delete()
    deps.save_config({"AMBIENT_API_KEY": None, "AMBIENT_KEY_BACKEND": None})
    if keychain_ok and not deps.keychain_read():
        print("• API key removed (keychain + env file).")
        return
    print("• Could not fully remove the key from the OS keychain (locked?). "
          f"Remove it manually: Keychain Access → search '{deps.keychain_service}'.",
          file=sys.stderr)


def _remove_launcher(args, deps):
    link_dir = getattr(args, "dir", None)
    try:
        deps.command_link(argparse.Namespace(remove=True, dir=link_dir))
    except SystemExit as error:
        print(f"• Launcher not removed: {error}", file=sys.stderr)
    if link_dir is None:
        print("• If you ran `ambient-codex link --dir <other>`, remove that one "
              "with `ambient-codex uninstall --dir <other>`.")


def _remove_state(purge, deps):
    if not purge:
        print(f"• Kept your settings and usage history in {deps.state_dir} "
              "(run with --purge to delete them).")
        return
    root = os.path.realpath(deps.state_dir)
    if deps.foreign_root(root) is not None:
        print(f"• Refusing to delete {root} — it is inside another install's tree.",
              file=sys.stderr)
    elif not os.path.isdir(root):
        print(f"• No state dir to delete ({root}).")
    else:
        _delete_state_tree(root)


def _delete_state_tree(root):
    errors = []
    shutil.rmtree(root, onerror=lambda _fn, path, _exc: errors.append(path))
    if not os.path.exists(root) and not errors:
        print(f"• Deleted all state: {root}")
    else:
        print(f"• Could not fully delete {root} ({len(errors)} item(s) left) — "
              "remove it manually.", file=sys.stderr)


__all__ = ("CacheDependencies", "UninstallDependencies", "UsageDependencies",
           "empty_usage_payload", "print_empty_usage", "run_cache",
           "run_uninstall", "run_usage")
