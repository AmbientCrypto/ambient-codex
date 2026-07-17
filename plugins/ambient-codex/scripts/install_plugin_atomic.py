#!/usr/bin/env python3
"""Stage Ambient Codex outside disposable caches and switch with rollback."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import NamedTuple


PLUGIN_NAME = "ambient-codex"
MARKETPLACE_NAME = "ambient-codex"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
DEFAULT_ROLLBACK_SOURCE = "https://github.com/AmbientCrypto/ambient-codex.git"
DEFAULT_ROLLBACK_REF = "v1"


class StageResult(NamedTuple):
    destination: Path
    version: str


def _read_pattern(path, pattern, label):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"unable to read {label}: {error}") from error
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"unable to find {label} version in {path}")
    return match.group(1)


def read_synchronized_versions(plugin_root):
    """Return each package version surface without changing the plugin."""
    root = Path(plugin_root)
    try:
        manifest = json.loads(
            (root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"unable to read plugin manifest: {error}") from error
    return {
        "manifest": manifest.get("version"),
        "pyproject": _read_pattern(
            root / "pyproject.toml", r'^version\s*=\s*"([^"]+)"', "pyproject"
        ),
        "cli": _read_pattern(
            root / "bin" / "ambient", r'^__version__\s*=\s*"([^"]+)"', "CLI"
        ),
        "mcp": _read_pattern(
            root / "mcp" / "ambient_mcp.py",
            r'^SERVER_VERSION\s*=\s*"([^"]+)"',
            "MCP",
        ),
    }


def _replace_pattern(path, pattern, replacement, label):
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"unable to update {label} version in {path}")
    path.write_text(updated, encoding="utf-8")


def _rewrite_versions(plugin_root, version):
    root = Path(plugin_root)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(
        json.dumps({**manifest, "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    _replace_pattern(
        root / "pyproject.toml",
        r'^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        "pyproject",
    )
    _replace_pattern(
        root / "bin" / "ambient",
        r'^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{version}"',
        "CLI",
    )
    _replace_pattern(
        root / "mcp" / "ambient_mcp.py",
        r'^SERVER_VERSION\s*=\s*"[^"]+"',
        f'SERVER_VERSION = "{version}"',
        "MCP",
    )


def _copy_ignore(_directory, names):
    excluded = {".git", ".codex-marketplace-install.json", "__pycache__"}
    return tuple(name for name in names if name in excluded or name.endswith(".pyc"))


def _validate_python_sources(plugin_root):
    for path in Path(plugin_root).rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, UnicodeError, SyntaxError) as error:
            raise ValueError(f"invalid Python source {path}: {error}") from error


def _default_validator(marketplace_root):
    root = Path(marketplace_root)
    plugin = root / "plugins" / PLUGIN_NAME
    marketplace = root / ".agents" / "plugins" / "marketplace.json"
    try:
        market_data = json.loads(marketplace.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"invalid marketplace manifest: {error}") from error
    if market_data.get("name") != MARKETPLACE_NAME:
        raise ValueError(f"marketplace name must equal {MARKETPLACE_NAME}")
    versions = read_synchronized_versions(plugin)
    if len(set(versions.values())) != 1 or not next(iter(versions.values()), None):
        raise ValueError(f"plugin versions are not synchronized: {versions}")
    _validate_python_sources(plugin)


def _inside(candidate, parent):
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _cached_version(base_version, cachebuster):
    base = str(base_version or "").split("+", 1)[0]
    if not base or not re.fullmatch(r"[A-Za-z0-9.-]+", cachebuster):
        raise ValueError("base version and cachebuster must be non-empty and safe")
    return f"{base}+codex.{cachebuster}"


def _atomic_activate(staging, destination):
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def stage_marketplace(source, destination, *, cachebuster, validator=None):
    """Copy, version, validate, and atomically activate a stable marketplace."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if _inside(destination, source):
        raise ValueError("destination must be outside the source marketplace tree")
    plugin = source / "plugins" / PLUGIN_NAME
    versions = read_synchronized_versions(plugin)
    if len(set(versions.values())) != 1:
        raise ValueError(f"source versions are not synchronized: {versions}")
    version = _cached_version(versions["manifest"], cachebuster)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True, ignore=_copy_ignore)
        _rewrite_versions(staging / "plugins" / PLUGIN_NAME, version)
        (validator or _default_validator)(staging)
        _atomic_activate(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return StageResult(destination=destination, version=version)


def _run_json(argv):
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"{' '.join(argv)} failed: {detail}")
    try:
        return json.loads(completed.stdout or "{}")
    except ValueError as error:
        raise RuntimeError(f"{' '.join(argv)} returned invalid JSON") from error


def _rollback_marketplace(runner, source, ref):
    commands = (
        ["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"],
        ["codex", "plugin", "marketplace", "add", source, "--ref", ref, "--json"],
        ["codex", "plugin", "add", PLUGIN_SELECTOR, "--json"],
    )
    for command in commands:
        try:
            runner(command)
        except Exception:
            continue


def _verify_install(payload, expected_version):
    installed = payload.get("installed") if isinstance(payload, dict) else None
    matches = (
        item for item in installed or []
        if isinstance(item, dict) and item.get("pluginId") == PLUGIN_SELECTOR
    )
    match = next(matches, None)
    if not match or not match.get("installed") or match.get("version") != expected_version:
        raise RuntimeError(
            f"installed plugin verification failed; expected {expected_version}, got {match}"
        )


def switch_marketplace(destination, *, expected_version, runner=None,
                       rollback_source=DEFAULT_ROLLBACK_SOURCE,
                       rollback_ref=DEFAULT_ROLLBACK_REF):
    """Switch Codex to a validated local marketplace or restore the remote."""
    run = runner or _run_json
    try:
        run(["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"])
        run(["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"])
        run(["codex", "plugin", "marketplace", "add", str(destination), "--json"])
        run(["codex", "plugin", "add", PLUGIN_SELECTOR, "--json"])
        listing = run(
            ["codex", "plugin", "list", "--marketplace", MARKETPLACE_NAME, "--json"]
        )
        _verify_install(listing, expected_version)
    except Exception:
        _rollback_marketplace(run, rollback_source, rollback_ref)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="stable Ambient marketplace source")
    parser.add_argument("destination", type=Path, help="stable local marketplace destination")
    parser.add_argument("--cachebuster")
    parser.add_argument("--switch", action="store_true", help="switch Codex after validation")
    args = parser.parse_args(argv)
    token = args.cachebuster or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    result = stage_marketplace(args.source, args.destination, cachebuster=token)
    if args.switch:
        switch_marketplace(result.destination, expected_version=result.version)
    print(json.dumps({"destination": str(result.destination), "version": result.version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
