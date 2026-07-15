"""Secure Ambient API-key onboarding, verification, and offboarding."""

import os
import sys
import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class SetupDependencies:
    preamble: str
    launcher_name: str
    key_console_url: str
    support_line: str
    default_api_url: str
    config_path: str
    api_key_env: str
    keychain_service: str
    read_config: object
    save_config: object
    resolve_key: object
    resolve_api_url: object
    key_paste_problem: object
    api_request: object
    classify_error: object
    catalog_data: object
    auth_probe: object
    network_error: object
    keychain_available: object
    keychain_write: object
    keychain_delete: object
    keychain_read: object
    secret_backend: object
    print_welcome: object


def normalize_pasted_key(value):
    key = value.strip()
    return key[7:].strip() if key.lower().startswith("bearer ") else key


def environment_variable_is_set(name):
    """Check secret configuration by name without reading its value."""
    return name in os.environ


def environment_override_note():
    """Return constant guidance that cannot reflect credential metadata."""
    return ("note: an Ambient Codex key override is still set in this shell's "
            "environment and will keep working until you unset it.")


def collect_key_interactive(deps):
    import getpass
    import warnings
    _verify_hidden_input_available(deps.launcher_name)
    print(deps.preamble)
    for attempt in range(3):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                key = getpass.getpass("Ambient API key (input hidden): ")
            except (EOFError, KeyboardInterrupt):
                raise SystemExit("\nambient: cancelled — nothing saved")
        if any(isinstance(item.message, getpass.GetPassWarning)
               for item in caught):
            raise SystemExit(
                "ambient: this terminal echoed the input — treat that key as "
                f"exposed: rotate it at {deps.key_console_url}, then use: "
                f"{deps.launcher_name} setup --key-stdin")
        key = normalize_pasted_key(key)
        problem = deps.key_paste_problem(key)
        if problem is None:
            return key
        more = "" if attempt == 2 else "  Try again (Ctrl-C quits)."
        print(f"  {problem}{more}")
    raise SystemExit("ambient: no valid key after 3 attempts — nothing saved")


def _verify_hidden_input_available(launcher_name):
    if os.name == "nt":
        return
    try:
        import termios
        termios.tcgetattr(sys.stdin.fileno())
    except (ImportError, OSError, ValueError):
        raise SystemExit(
            "ambient: this terminal cannot hide typed input — the key would be "
            f"visible. Pipe it instead: {launcher_name} setup --key-stdin")


def verify_and_store_key(key, conf, use_file, deps):
    api_url = deps.resolve_api_url(conf)
    host = urllib.parse.urlsplit(api_url).hostname
    print(f"Verifying key with {host} (one tiny paid completion)…",
          file=sys.stderr)
    models, ok, category, detail = _verify_key_request(
        api_url, key, deps)
    funds_issue = _handle_probe_result(ok, category, detail, deps)
    where = _store_verified_key(key, api_url, use_file, deps)
    return models, detail, where, funds_issue


def _verify_key_request(api_url, key, deps):
    try:
        status, body = deps.api_request(
            api_url, key, "/v1/models", timeout=30)
        if status != 200:
            category, diagnosis = deps.classify_error(status, body, key)
            raise SystemExit(
                f"ambient [{category}]: {diagnosis} Nothing was saved.\n"
                f"{deps.support_line}")
        models = deps.catalog_data(body)
        ok, category, detail = deps.auth_probe(api_url, key, models)
        return models, ok, category, detail
    except deps.network_error as error:
        raise SystemExit(
            f"ambient [network]: cannot reach Ambient ({error}). Check your "
            "internet connection and try again — nothing saved; if other sites "
            f"load fine, Ambient itself may be unreachable.\n{deps.support_line}")


def _handle_probe_result(ok, category, detail, deps):
    if ok:
        return False
    if category == "funds":
        return True
    if category == "key":
        raise SystemExit(
            "ambient [key]: Ambient rejected this key — it may be mistyped, "
            f"revoked, or inactive. Re-copy it from {deps.key_console_url} → "
            f"API Keys, or create a fresh one. Nothing was saved.\n{deps.support_line}")
    if category in ("rate", "service"):
        raise SystemExit(
            f"ambient [{category}]: could not verify the key right now "
            f"({detail}) — an Ambient-side problem, not your key. Nothing was "
            f"saved. Retry in a minute: {deps.launcher_name} setup\n"
            f"{deps.support_line}")
    raise SystemExit(
        f"ambient [{category}]: key check FAILED: {detail} — nothing saved.\n"
        f"{deps.support_line}")


def _store_verified_key(key, api_url, use_file, deps):
    api_value = api_url if api_url != deps.default_api_url else None
    if not use_file and deps.keychain_available():
        if not deps.keychain_write(key):
            raise SystemExit(
                "ambient: Keychain write failed — nothing saved. Retry, or store "
                f"in the env file explicitly with: {deps.launcher_name} setup "
                "--force --file")
        deps.save_config({"AMBIENT_API_KEY": None, "AMBIENT_API_URL": api_value,
                          "AMBIENT_KEY_BACKEND": "keychain"})
        return ("macOS Keychain (no plaintext on disk)"
                if deps.secret_backend() == "keychain"
                else "OS secret store (no plaintext on disk)")
    if use_file:
        deps.keychain_delete()
    deps.save_config({"AMBIENT_API_KEY": key, "AMBIENT_API_URL": api_value,
                      "AMBIENT_KEY_BACKEND": "file"})
    return f"{deps.config_path} (owner-only permissions)"


def setup_remove(deps):
    keychain_ok = deps.keychain_delete()
    deps.save_config({"AMBIENT_API_KEY": None, "AMBIENT_KEY_BACKEND": None})
    if not keychain_ok:
        raise SystemExit(
            "ambient: the env file was scrubbed, but the OS secret store REFUSED "
            "the delete (locked keychain?) — the key may still be stored there. "
            f"Unlock your keychain and re-run: {deps.launcher_name} setup --remove")
    if deps.keychain_read():
        raise SystemExit(
            "ambient: the key still resolves from the OS secret store after the "
            "delete — remove it manually (Keychain Access → search "
            f"'{deps.keychain_service}') or re-run: {deps.launcher_name} setup --remove")
    print("Key removed from the OS secret store and the env file.")
    print("Sticky settings (default models, delegate mode) were kept — delete")
    print(f"{deps.config_path} to reset everything. Reconfigure any time: "
          f"{deps.launcher_name} setup")
    if environment_variable_is_set(deps.api_key_env):
        print(environment_override_note(), file=sys.stderr)


def run_setup(args, deps):
    conf = deps.read_config()
    if getattr(args, "remove", False):
        setup_remove(deps)
        return
    existing, backend = deps.resolve_key(conf)
    if existing and not args.force:
        _refuse_existing_key(backend, deps)
    key = _collect_setup_key(args, deps)
    models, detail, where, funds_issue = verify_and_store_key(
        key, conf, args.file, deps)
    print()
    deps.print_welcome(
        models, where, detail, deps.read_config(), funds_issue)


def _refuse_existing_key(backend, deps):
    where = {"keychain": "macOS Keychain", "secret-tool": "OS secret store",
             "file": deps.config_path,
             "env": f"the {deps.api_key_env} environment variable"}.get(
                 backend, backend or "unknown")
    raise SystemExit(
        f"ambient: a key is already configured ({where}).\n"
        f"  Rotate/replace: {deps.launcher_name} setup --force\n"
        f"  Remove:         {deps.launcher_name} setup --remove")


def _collect_setup_key(args, deps):
    if args.key_stdin:
        try:
            stdin_is_tty = sys.stdin.isatty()
        except Exception:  # noqa: BLE001
            stdin_is_tty = False
        if stdin_is_tty:
            raise SystemExit(
                "ambient: --key-stdin expects a piped key (echo-safe). For "
                f"interactive entry with hidden input, run: {deps.launcher_name} setup")
        key = normalize_pasted_key(sys.stdin.readline())
        problem = deps.key_paste_problem(key)
        if problem:
            raise SystemExit(f"ambient: {problem} — nothing saved")
        return key
    if sys.stdin.isatty():
        return collect_key_interactive(deps)
    raise SystemExit(
        f"ambient: no TTY — pipe the key via: {deps.launcher_name} setup --key-stdin")


__all__ = ("SetupDependencies", "collect_key_interactive",
           "environment_override_note", "environment_variable_is_set",
           "normalize_pasted_key", "run_setup", "setup_remove",
           "verify_and_store_key")
