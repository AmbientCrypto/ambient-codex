#!/usr/bin/env python3
"""Stable user-facing launcher for a cache-versioned Ambient Codex plugin."""

import json
import os
import shutil
import subprocess
import sys


MARKER = "ambient-codex stable launcher v1"
CODEX_TIMEOUT_SECONDS = 10


def fail(message):
    print(f"ambient-codex: {message}", file=sys.stderr)
    return 1


def active_cli():
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex is not on PATH; reinstall the plugin with Codex")
    try:
        result = subprocess.run(
            [codex, "mcp", "get", "ambient", "--json"],
            capture_output=True, text=True, timeout=CODEX_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("could not inspect the active Codex plugin") from error
    if result.returncode != 0:
        raise RuntimeError("Ambient is not enabled in Codex; reinstall the plugin")
    try:
        payload = json.loads(result.stdout)
        cwd = payload["transport"]["cwd"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Codex returned an invalid Ambient plugin location") from error
    if not isinstance(cwd, str) or not cwd:
        raise RuntimeError("Codex returned an invalid Ambient plugin location")
    root = os.path.realpath(cwd)
    target = os.path.join(root, "bin", "ambient")
    marker = f"{os.sep}ambient-codex{os.sep}"
    if marker not in root + os.sep or not os.path.isfile(target):
        raise RuntimeError("Codex did not report a valid Ambient plugin location")
    return target


def main():
    try:
        target = active_cli()
    except RuntimeError as error:
        return fail(str(error))
    argv = [sys.executable, target, *sys.argv[1:]]
    if os.name == "nt":
        return subprocess.run(argv, check=False).returncode
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    raise SystemExit(main())
