"""Runtime state, credentials, trusted backend, and configuration loading."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class RuntimeDependencies:
    bindings: Mapping[str, object]

    @classmethod
    def bind(cls, **bindings):
        return cls(MappingProxyType(dict(bindings)))

    def __getattr__(self, name):
        try:
            return self.bindings[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _resolve(path, deps=None):
    """realpath so `..`, symlinks, and `/private` aliasing cannot dodge a prefix check."""
    _state_core = deps._state_core
    return _state_core.resolve(path)


def _is_within(child, parent, deps=None):
    """True when `child` IS `parent` or lives beneath it. Prefix-safe."""
    _state_core = deps._state_core
    return _state_core.is_within(child, parent)


def foreign_root(path, deps=None):
    """The foreign tree `path` falls inside, or None."""
    FOREIGN_STATE_DIRS = deps.FOREIGN_STATE_DIRS
    _state_core = deps._state_core
    return _state_core.foreign_root(path, FOREIGN_STATE_DIRS)


def validate_state_root(root, deps=None):
    """Reject a state root that is not ours. Returns the root, or exits.

    `AMBIENT_CODEX_HOME=~/.config/ambient` used to make this install read the other
    install's key and rewrite its delegate mode, and `~/.config/ambient/cache` dodged an
    exact-match check. Compare on realpath, at any depth.
    """
    AMBIENT_CODEX_HOME_ENV = deps.AMBIENT_CODEX_HOME_ENV
    FOREIGN_STATE_DIRS = deps.FOREIGN_STATE_DIRS
    STATE_MARKER = deps.STATE_MARKER
    _state_core = deps._state_core
    sys = deps.sys
    error = _state_core.state_root_error(
        root, FOREIGN_STATE_DIRS, STATE_MARKER, AMBIENT_CODEX_HOME_ENV
    )
    if error is not None:
        sys.exit(error)
    return root


def _state_dir(deps=None):
    """This install's state root. AMBIENT_CODEX_HOME may relocate it, never onto another
    Ambient install."""
    AMBIENT_CODEX_HOME_ENV = deps.AMBIENT_CODEX_HOME_ENV
    os = deps.os
    validate_state_root = deps.validate_state_root
    override = os.environ.get(AMBIENT_CODEX_HOME_ENV)
    if not override:
        return os.path.expanduser("~/.config/ambient-codex")
    return validate_state_root(os.path.abspath(os.path.expanduser(override)))


def _claim_state_dir(conf_dir, deps=None):
    """Stamp our marker so the root is recognisably ours on a later run."""
    STATE_DIR = deps.STATE_DIR
    STATE_MARKER = deps.STATE_MARKER
    __version__ = deps.__version__
    _config_store = deps._config_store
    return _config_store.claim_state_dir(
        conf_dir, STATE_DIR, STATE_MARKER, __version__
    )


def _env_pos_int(name, default, floor=1, deps=None):
    """A positive-int env override; a missing/invalid/too-small value keeps the
    default (with a sane floor so a fat-fingered '0' can't disable a guard)."""
    os = deps.os
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(floor, int(raw))
    except (TypeError, ValueError):
        return default


def normalize_requested_max_tokens(value, deps=None):
    """Apply only the CLI-wide safety ceiling.

    The selected model's live profile performs the meaningful context/output
    clamp later.  Keeping this boundary above the catalog's current frontier
    range lets an explicit request reach that per-model resolver instead of
    being discarded at 200,000 for every model.
    """
    MAX_REQUESTED_TOKENS = deps.MAX_REQUESTED_TOKENS
    sys = deps.sys
    if value is None:
        return None
    if value > MAX_REQUESTED_TOKENS:
        print(
            f"ambient: --max-tokens capped at {MAX_REQUESTED_TOKENS}",
            file=sys.stderr,
        )
        return MAX_REQUESTED_TOKENS
    return value


def read_config_file(deps=None):
    CONFIG_PATH = deps.CONFIG_PATH
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    _config_store = deps._config_store
    os = deps.os
    sys = deps.sys
    return _config_store.read_config_file(
        CONFIG_PATH, LAUNCHER_NAME, sys.stderr, os.name
    )


def secret_backend(deps=None):
    """Which OS secret store is usable (C1): macOS Keychain, Linux libsecret
    (secret-tool), or None → the 0600 env-file fallback. Never fails closed like
    the old Mac-only path that silently dropped everyone else to plaintext."""
    _credentials_core = deps._credentials_core
    shutil = deps.shutil
    sys = deps.sys
    return _credentials_core.secret_backend(sys.platform, shutil.which)


def keychain_available(deps=None):
    _credentials_core = deps._credentials_core
    secret_backend = deps.secret_backend
    return _credentials_core.keychain_available(secret_backend())


def keychain_read(deps=None):
    """Fetch THIS install's key from the OS secret store. None when absent.

    Deliberately takes no service argument: there is no supported way to point this at
    another Ambient install's keychain item.
    """
    KEYCHAIN_ACCOUNT = deps.KEYCHAIN_ACCOUNT
    KEYCHAIN_SERVICE = deps.KEYCHAIN_SERVICE
    _credentials_core = deps._credentials_core
    secret_backend = deps.secret_backend
    subprocess = deps.subprocess
    return _credentials_core.keychain_read(
        secret_backend(), subprocess.run, KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT
    )


def keychain_write(key, deps=None):
    """Store the key in the OS secret store, passing the secret over STDIN so it
    never appears in any process's argv. Returns True on success."""
    KEYCHAIN_ACCOUNT = deps.KEYCHAIN_ACCOUNT
    KEYCHAIN_SERVICE = deps.KEYCHAIN_SERVICE
    _credentials_core = deps._credentials_core
    secret_backend = deps.secret_backend
    subprocess = deps.subprocess
    return _credentials_core.keychain_write(
        key, secret_backend(), subprocess.run, KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT
    )


def keychain_delete(deps=None):
    """Remove the stored key from the OS secret store. Returns True when the
    key is gone (deleted or was absent) and False on a real failure — a locked
    Keychain must not let `setup --remove` claim success while the key still
    resolves."""
    KEYCHAIN_ACCOUNT = deps.KEYCHAIN_ACCOUNT
    KEYCHAIN_SERVICE = deps.KEYCHAIN_SERVICE
    _credentials_core = deps._credentials_core
    secret_backend = deps.secret_backend
    subprocess = deps.subprocess
    return _credentials_core.keychain_delete(
        secret_backend(), subprocess.run, KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT
    )


def shared_key_env_is_set(deps=None):
    """True when the cross-install `AMBIENT_API_KEY` is exported. We IGNORE it."""
    SHARED_API_KEY_ENV = deps.SHARED_API_KEY_ENV
    _credentials_core = deps._credentials_core
    os = deps.os
    return _credentials_core.shared_key_env_is_set(
        os.environ.get(SHARED_API_KEY_ENV)
    )


def resolve_key_and_backend(conf, deps=None):
    """Key lookup: AMBIENT_CODEX_API_KEY > Keychain > config file.

    `AMBIENT_API_KEY` is deliberately NOT consulted. Every Ambient install reads that
    name, so honouring it would hand one key to all of them — the exact coupling this
    install exists to avoid. An explicit AMBIENT_KEY_BACKEND=file skips the Keychain so
    `setup --file` can't be shadowed by a stale Keychain entry."""
    API_KEY_ENV = deps.API_KEY_ENV
    _credentials_core = deps._credentials_core
    keychain_read = deps.keychain_read
    os = deps.os
    secret_backend = deps.secret_backend
    return _credentials_core.resolve_key_and_backend(
        conf, os.environ.get(API_KEY_ENV), keychain_read, secret_backend
    )


def _is_local_host(host, deps=None):
    """True ONLY for a loopback host. The AMBIENT_ALLOW_INSECURE local-dev
    escape may point the key-bearing endpoint here without a recorded trust
    decision. Deliberately NOT is_private / is_link_local / 0.0.0.0: an RFC-1918
    or link-local address can be a REMOTE box on a LAN/VPN, and this must never
    whitelist a non-local host (a single injected AMBIENT_API_URL would then
    exfil the key). A non-loopback dev gateway must use `ambient-codex trust-url`.
    Residual: a rebound `localhost` needs host-file/DNS control — a deeper
    compromise than the injected-env-var threat this guard addresses."""
    import ipaddress
    h = (host or "").lower().strip("[]")   # tolerate [::1]-style IPv6 literals
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def resolve_api_url(conf, deps=None):
    DEFAULT_API_URL = deps.DEFAULT_API_URL
    _argv_command = deps._argv_command
    _fail_exit = deps._fail_exit
    _is_local_host = deps._is_local_host
    os = deps.os
    urllib = deps.urllib
    from_env = os.environ.get("AMBIENT_API_URL")
    api_url = (from_env or conf.get("AMBIENT_API_URL") or DEFAULT_API_URL).rstrip("/")
    parts = urllib.parse.urlsplit(api_url)
    host = (parts.hostname or "").lower()
    # AMBIENT_ALLOW_INSECURE relaxes the HTTPS requirement ONLY for a LOCAL host
    # (its documented local-dev purpose) — NEVER for a real/public host, so the
    # Bearer key can't ride plaintext HTTP to api.ambient.xyz or an attacker's box.
    allow_insecure_local = (os.environ.get("AMBIENT_ALLOW_INSECURE") == "1"
                            and _is_local_host(host))
    if parts.scheme.lower() != "https" and not allow_insecure_local:
        _fail_exit(
            None, _argv_command(), "config",
            f"refusing non-HTTPS endpoint {api_url} — the key would travel "
            "unencrypted. Use https:// (AMBIENT_ALLOW_INSECURE=1 only relaxes this "
            "for a LOCAL endpoint like http://127.0.0.1)."
        )
    # HOST PINNING: the API key rides in the Authorization header of every call,
    # so AMBIENT_API_URL is key-equivalent — an injected env var or a tampered
    # config line pointing anywhere else is a one-call key exfil. Only Ambient
    # hosts are trusted implicitly; anything else needs an explicit, recorded
    # trust decision (ambient trust-url), and an ENV-sourced override needs a
    # second env var so a single injected variable is never sufficient.
    host = (parts.hostname or "").lower()
    # AMBIENT_ALLOW_INSECURE bypasses host pinning ONLY for a local host (its
    # documented local-dev purpose) — never for an arbitrary public host, so a
    # single injected AMBIENT_API_URL=https://evil.com can't exfil the key.
    if host == "ambient.xyz" or host.endswith(".ambient.xyz") \
            or (os.environ.get("AMBIENT_ALLOW_INSECURE") == "1"
                and _is_local_host(host)):
        return api_url
    if conf.get("AMBIENT_TRUSTED_URL") == api_url and (
            not from_env or os.environ.get("AMBIENT_ALLOW_URL") == "1"):
        return api_url
    _fail_exit(
        None, _argv_command(), "config",
        f"refusing to send your API key to non-Ambient endpoint "
        f"'{host or api_url}'. If this is deliberate (a self-hosted gateway), "
        "record the trust decision first: ambient-codex trust-url <url>"
    )


def cmd_trust_url(args, deps=None):
    """Record an explicit trust decision for a non-Ambient endpoint. The key
    is sent to whatever host AMBIENT_API_URL names, so this is key-equivalent
    and demands a typed confirmation on a real terminal."""
    save_config_values = deps.save_config_values
    sys = deps.sys
    urllib = deps.urllib
    url = args.url.rstrip("/")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or not host:
        sys.exit("ambient: trust-url needs a full https:// URL")
    if not sys.stdin.isatty():
        sys.exit("ambient: trust-url is interactive-only (a typed confirmation "
                 "proves a human made this decision)")
    # M32: interactive prompts + status go to STDERR (input() defaults its
    # prompt to stdout) so a caller capturing stdout gets clean, parseable output.
    print(
        f"Your Ambient API key will be sent in the Authorization header to\n"
        f"  {host}\non EVERY future call. Only do this for a gateway you run "
        "or fully trust.", file=sys.stderr
    )
    try:
        sys.stderr.write(f"Type the hostname ({host}) to confirm: ")
        sys.stderr.flush()
        typed = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nambient: cancelled")
    if typed != host:
        sys.exit("ambient: hostname mismatch — nothing saved")
    save_config_values({"AMBIENT_TRUSTED_URL": url, "AMBIENT_API_URL": url})
    print(f"Trusted endpoint saved: {url}\nRevert any time: ambient-codex trust-url --reset",
          file=sys.stderr)


def cmd_trust_url_reset(deps=None):
    DEFAULT_API_URL = deps.DEFAULT_API_URL
    save_config_values = deps.save_config_values
    save_config_values({"AMBIENT_TRUSTED_URL": None, "AMBIENT_API_URL": None})
    print(f"Endpoint trust cleared — back to {DEFAULT_API_URL}")


def exit_unconfigured(launcher_name, exit_code, sys_module):
    """Exit with constant setup guidance; this boundary never accepts a key."""
    print(
        "ambient [setup]: no API key configured.\n"
        f"  Interactive:      {launcher_name} setup\n"
        f"  Non-interactive:  {launcher_name} setup --key-stdin\n"
        "  Get a key:        https://ambient.xyz",
        file=sys_module.stderr,
    )
    sys_module.exit(exit_code)


def load_config(deps=None):
    """Read key/url/defaults, letting env vars win, preferring Keychain storage.
    FIRST USE on a real terminal onboards inline (asks for the key, verifies,
    shows the welcome panel, then continues the original command) instead of
    erroring; non-interactive callers get a stable category + dedicated exit
    code and never hang."""
    EXIT_UNCONFIGURED = deps.EXIT_UNCONFIGURED
    LAUNCHER_NAME = deps.LAUNCHER_NAME
    argparse = deps.argparse
    cmd_setup = deps.cmd_setup
    os = deps.os
    read_config_file = deps.read_config_file
    resolve_api_url = deps.resolve_api_url
    resolve_key_and_backend = deps.resolve_key_and_backend
    sys = deps.sys
    conf = read_config_file()
    api_key, _ = resolve_key_and_backend(conf)
    if api_key:
        # A13: validate the URL only when a key EXISTS to protect. A first-run
        # user with a custom AMBIENT_API_URL but no key must reach onboarding
        # below — not be blocked by the key-exfil refusal for a key that does
        # not exist yet (the refusal fires again, correctly, once a key is set).
        return api_key, resolve_api_url(conf), conf
    # stdout.isatty matters too: `ambient ask --json > out.json` on a first
    # run must NOT interleave the setup preamble/welcome panel into the
    # captured file — a redirected first run gets exit 3.
    interactive = (
        sys.stdin.isatty() and sys.stderr.isatty() and sys.stdout.isatty()
        and os.environ.get("AMBIENT_NO_ONBOARD") != "1"
        and not os.environ.get("CI")
    )
    if interactive:
        print(
            "ambient: first run — no API key is configured yet. Setting one "
            "up takes about a minute.\n",
            file=sys.stderr,
        )
        cmd_setup(argparse.Namespace(
            key_stdin=False, force=False, file=False, remove=False))
        conf = read_config_file()
        api_key, _ = resolve_key_and_backend(conf)
        if api_key:
            print("\nambient: setup complete — continuing with your original "
                  "command.\n", file=sys.stderr)
            return api_key, resolve_api_url(conf), conf
    exit_unconfigured(LAUNCHER_NAME, EXIT_UNCONFIGURED, sys)
