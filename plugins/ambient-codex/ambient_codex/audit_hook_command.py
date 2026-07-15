"""Managed Git audit-hook installation and removal."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AuditHookDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _render_hook(name, deps=None):
    """The fixed git-hook script for `name` (pre-commit|pre-push). Threshold
    (documented in the script itself): BLOCK only on verdict "FIX FIRST"
    (CRITICAL/HIGH findings); AMBIENT_HOOK_MODE=warn makes it report-only.
    Everything else — audit unavailable, unconfigured, nothing staged,
    network down — FAILS OPEN: a review hook must never brick commits."""
    AMBIENT_HOOK_MARKER = deps.AMBIENT_HOOK_MARKER
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    _bundled_cli_path = deps._bundled_cli_path
    shlex = deps.shlex
    if name == "pre-commit":
        audit_args = "--staged --json"
        what = "staged diff"
        bypass = "git commit --no-verify"
    else:
        audit_args = '--diff "@{u}...HEAD" --json'
        what = "outgoing commits (upstream...HEAD)"
        bypass = "git push --no-verify"
    # Resolve THIS install's CLI, never a bare `ambient` on PATH: that name may
    # belong to a different Ambient install, whose key, model lanes, and usage
    # ledger the hook would then silently use. The versioned plugin cache dir
    # moves on every update, so prefer the stable `ambient-codex` launcher and
    # fall back to the absolute path recorded at install time.
    bundled = shlex.quote(_bundled_cli_path())
    return f"""#!/bin/sh
{AMBIENT_HOOK_MARKER} ({name})
# Installed by: {LAUNCHER_NAME} audit --install-hook {name}
# FIXED script - it only RUNS `ambient-codex audit` on the {what}
# and reads the verdict; it never contains or executes model output.
#
# Threshold: BLOCKS only on verdict "FIX FIRST" (CRITICAL/HIGH findings).
#   AMBIENT_HOOK_MODE=warn      report findings but never block
#   bypass once:                {bypass}
#   uninstall:                  ambient-codex audit --uninstall-hook {name}
AMBIENT_BIN=""
if command -v ambient-codex >/dev/null 2>&1; then
    AMBIENT_BIN=ambient-codex
elif [ -x {bundled} ]; then
    AMBIENT_BIN={bundled}
else
    echo "ambient-hook: ambient-codex not found - skipping audit" >&2
    exit 0
fi
out=$("$AMBIENT_BIN" audit {audit_args} --yes 2>/dev/null)
rc=$?
if [ -z "$out" ]; then
    echo "ambient-hook: audit produced no output (rc=$rc) - skipping (fail-open)" >&2
    exit 0
fi
printf '%s\\n' "$out"
if printf '%s' "$out" | grep -q '"verdict": "FIX FIRST"'; then
    if [ "$AMBIENT_HOOK_MODE" = "warn" ]; then
        echo "ambient-hook: verdict FIX FIRST - warn-only mode (AMBIENT_HOOK_MODE=warn), not blocking" >&2
        exit 0
    fi
    echo "ambient-hook: verdict FIX FIRST - blocking. Bypass once: {bypass}; report-only: AMBIENT_HOOK_MODE=warn" >&2
    exit 1
fi
exit 0
"""


def _git_hooks_dir(args, deps=None):
    """Absolute hooks dir of the CURRENT repo (worktree-aware via
    `git rev-parse --git-path hooks`); clean usage error outside a repo."""
    EXIT_USAGE = deps.EXIT_USAGE
    _fail_exit = deps._fail_exit
    os = deps.os
    subprocess = deps.subprocess
    try:
        proc = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is None or proc.returncode != 0:
        _fail_exit(args, "audit", "usage",
                   "--install-hook/--uninstall-hook must run inside a git "
                   "repository.", exit_code=EXIT_USAGE)
    return os.path.abspath(proc.stdout.strip())


def _hook_is_ours(existing, name, deps=None):
    """STRICT ownership check: a hook is
    ambient's only when it is byte-identical to the current generated
    template, OR carries the exact generated header — shebang + the marker
    as the ENTIRE second line + the installed-by template line as the
    ENTIRE third line. Every line must match EXACTLY (no startswith): a
    foreign hook that merely SHARES the installed-by prefix (e.g. a fork
    appending its own note) is foreign — it needs --force to replace and
    is never auto-removed."""
    AMBIENT_HOOK_MARKER = deps.AMBIENT_HOOK_MARKER
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    LEGACY_AMBIENT_HOOK_MARKERS = deps.LEGACY_AMBIENT_HOOK_MARKERS
    _render_hook = deps._render_hook
    if existing is None:
        return False
    if existing == _render_hook(name):
        return True
    lines = existing.splitlines()
    markers = (AMBIENT_HOOK_MARKER, *LEGACY_AMBIENT_HOOK_MARKERS)
    # 1.5.x wrote the installed-by line as `ambient audit ...` before the launcher
    # was renamed. The MARKER line is what proves ownership and only we ever write
    # it, so accept either installed-by wording; refusing the old one would leave
    # upgraded users unable to uninstall their own hook.
    installed_by = (
        f"# Installed by: {LAUNCHER_NAME} audit --install-hook {name}",
        f"# Installed by: ambient audit --install-hook {name}",
    )
    return any(
        len(lines) >= 3
        and lines[0] == "#!/bin/sh"
        and lines[1] == f"{marker} ({name})"
        and lines[2] in installed_by
        for marker in markers
    )


def cmd_audit_hook(args, deps=None):
    """Install/remove the ambient-codex audit git hook. Pure hooks-file management:
    needs no API key and makes no network call. Refuses to CLOBBER a hook it
    did not install (strict template/header match — _hook_is_ours) unless
    --force (which backs the original up first); uninstall removes ONLY an
    ambient-installed hook."""
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    _fail_exit = deps._fail_exit
    _git_hooks_dir = deps._git_hooks_dir
    _hook_is_ours = deps._hook_is_ours
    _render_hook = deps._render_hook
    contextlib = deps.contextlib
    os = deps.os
    shutil = deps.shutil
    sys = deps.sys
    tempfile = deps.tempfile
    usage_exit = deps.usage_exit
    if getattr(args, "install_hook", None) and getattr(args, "uninstall_hook",
                                                       None):
        usage_exit("pass either --install-hook or --uninstall-hook, not both")
    installing = bool(getattr(args, "install_hook", None))
    name = args.install_hook if installing else args.uninstall_hook
    hooks_dir = _git_hooks_dir(args)
    path = os.path.join(hooks_dir, name)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            existing = fh.read()
    except OSError:
        existing = None
    ours = _hook_is_ours(existing, name)
    if not installing:
        if existing is None:
            print(f"ambient: no {name} hook installed — nothing to remove")
            return
        if not ours:
            _fail_exit(args, "audit", "config",
                       f"the existing {name} hook at {path} was NOT installed "
                       "by ambient — refusing to remove it.")
        os.unlink(path)
        print(f"ambient: removed the ambient {name} hook ({path})")
        return
    if existing is not None and not ours:
        if not getattr(args, "force", False):
            _fail_exit(args, "audit", "config",
                       f"a {name} hook already exists at {path} and was not "
                       "installed by ambient — re-run with --force to replace "
                       f"it (the original is saved to {name}.pre-ambient.bak).")
        backup = path + ".pre-ambient.bak"
        shutil.copy2(path, backup)
        print(f"ambient: existing {name} hook backed up to {backup}",
              file=sys.stderr)
    script = _render_hook(name)
    os.makedirs(hooks_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=hooks_dir, prefix=".tmp-hook-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    bypass = ("git commit --no-verify" if name == "pre-commit"
              else "git push --no-verify")
    audit_cmd = (f"{LAUNCHER_NAME} audit --staged --json" if name == "pre-commit"
                 else f'{LAUNCHER_NAME} audit --diff "@{{u}}...HEAD" --json')
    print(f"ambient: installed the {name} hook → {path}\n"
          f"  runs:        {audit_cmd}\n"
          "  blocks on:   verdict FIX FIRST (CRITICAL/HIGH findings); "
          "everything else passes\n"
          "  warn-only:   AMBIENT_HOOK_MODE=warn (reports, never blocks)\n"
          f"  bypass once: {bypass}\n"
          f"  uninstall:   ambient-codex audit --uninstall-hook {name}")
