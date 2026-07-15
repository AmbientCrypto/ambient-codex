"""Cache-rotation-safe PATH launcher installation and removal."""

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class LauncherDependencies:
    launcher_name: str
    stable_launcher_marker: str
    link_is_ours: object
    shim_is_ours: object
    stable_launcher_asset: object
    stable_launcher_is_ours: object
    write_stable_launcher: object


def write_stable_launcher(source, destination):
    """Atomically copy a standalone launcher without following links."""
    temporary = destination + f".tmp-{os.getpid()}"
    try:
        with open(source, encoding="utf-8") as reader:
            body = reader.read()
        with open(temporary, "x", encoding="utf-8", newline="") as writer:
            writer.write(body)
        os.chmod(temporary, 0o755)
        os.replace(temporary, destination)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def run_link(args, deps):
    destination_dir = os.path.expanduser(
        getattr(args, "dir", None) or "~/.local/bin")
    destination = os.path.join(destination_dir, deps.launcher_name)
    if getattr(args, "remove", False):
        _remove_launcher(destination_dir, destination, deps)
        return
    _refuse_foreign_destination(destination, deps)
    try:
        os.makedirs(destination_dir, exist_ok=True)
        asset = deps.stable_launcher_asset()
        if os.name == "nt":
            _install_windows_launcher(destination_dir, destination, asset, deps)
            return
        deps.write_stable_launcher(asset, destination)
    except OSError as error:
        raise SystemExit(f"ambient: could not create the link ({error})")
    print(f"linked: {destination} -> active Codex Ambient plugin")
    _note_missing_path(destination_dir, windows=False)


def _remove_launcher(destination_dir, destination, deps):
    if os.name == "nt":
        _remove_windows_launcher(destination_dir, destination, deps)
        return
    if os.path.islink(destination):
        _remove_posix_link(destination, deps)
    elif os.path.exists(destination):
        if not deps.stable_launcher_is_ours(destination):
            raise SystemExit(
                f"ambient: {destination} is not an ambient-codex launcher — "
                "refusing to delete a foreign file")
        os.remove(destination)
    else:
        print(f"nothing to remove at {destination}")


def _remove_windows_launcher(destination_dir, destination, deps):
    shim = os.path.join(destination_dir, deps.launcher_name + ".cmd")
    payload = destination + ".py"
    if not (os.path.exists(shim) or os.path.exists(payload)):
        print(f"nothing to remove at {shim}")
        return
    if os.path.exists(shim) and not deps.shim_is_ours(shim):
        raise SystemExit(
            f"ambient: {shim} is not an ambient-codex launcher — refusing to delete it")
    if os.path.exists(payload) and not deps.stable_launcher_is_ours(payload):
        raise SystemExit(
            f"ambient: {payload} is not an ambient-codex launcher — refusing to delete it")
    for path in (shim, payload):
        if os.path.exists(path):
            os.remove(path)
    print(f"removed: {shim}")


def _remove_posix_link(destination, deps):
    if deps.link_is_ours(destination):
        os.unlink(destination)
        print(f"removed: {destination}")
        return
    raise SystemExit(
        f"ambient: {destination} points at {os.readlink(destination)} — not "
        "an ambient-codex launcher, refusing to delete it")


def _refuse_foreign_destination(destination, deps):
    if (os.path.exists(destination) and not os.path.islink(destination)
            and not deps.stable_launcher_is_ours(destination)):
        raise SystemExit(
            f"ambient: {destination} exists and is not an ambient-codex launcher — "
            "refusing to overwrite it. Pass --dir DIR to link somewhere else.")
    if os.path.islink(destination) and not deps.link_is_ours(destination):
        raise SystemExit(
            f"ambient: {destination} is a symlink to another tool "
            f"({os.readlink(destination)}) — refusing to overwrite it. "
            "Pass --dir DIR to link somewhere else.")


def _install_windows_launcher(destination_dir, destination, asset, deps):
    shim = os.path.join(destination_dir, deps.launcher_name + ".cmd")
    payload = destination + ".py"
    if os.path.exists(shim) and not deps.shim_is_ours(shim):
        raise SystemExit(
            f"ambient: {shim} is not an ambient-codex launcher — refusing to overwrite it")
    if os.path.exists(payload) and not deps.stable_launcher_is_ours(payload):
        raise SystemExit(
            f"ambient: {payload} is not an ambient-codex launcher — refusing to overwrite it")
    deps.write_stable_launcher(asset, payload)
    with open(shim, "w", encoding="utf-8") as handle:
        handle.write(
            f"@rem {deps.stable_launcher_marker}\r\n"
            f'@"{sys.executable}" "%~dp0{deps.launcher_name}.py" %*\r\n')
    print(f"wrote launcher: {shim}")
    _note_missing_path(destination_dir, windows=True)


def _note_missing_path(destination_dir, windows):
    if destination_dir in os.environ.get("PATH", "").split(os.pathsep):
        return
    if windows:
        print(f"note: {destination_dir} is not on your PATH yet — add it so "
              "`ambient` works from any terminal (System Settings → "
              "Environment Variables → Path, or run:\n"
              f'  setx PATH "%PATH%;{destination_dir}")')
        return
    print(f"note: {destination_dir} is not on your PATH yet — add this to your "
          f'shell profile:\n  export PATH="{destination_dir}:$PATH"')


__all__ = ("LauncherDependencies", "run_link", "write_stable_launcher")
