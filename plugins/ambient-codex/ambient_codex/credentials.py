"""Explicit credential-store adapters with no import-time external effects."""

import subprocess


def secret_backend(platform_name, executable_lookup):
    """Return the supported secret store available on the supplied platform."""
    if platform_name == "darwin" and executable_lookup("security"):
        return "keychain"
    if platform_name.startswith("linux") and executable_lookup("secret-tool"):
        return "secret-tool"
    return None


def keychain_available(backend):
    """Return whether backend names a supported OS credential store."""
    return backend in ("keychain", "secret-tool")


def keychain_read(backend, runner, service, account):
    """Read one credential from the explicitly selected store."""
    try:
        if backend == "keychain":
            proc = runner(
                ["security", "find-generic-password", "-s", service,
                 "-a", account, "-w"],
                capture_output=True, text=True, timeout=10,
            )
        elif backend == "secret-tool":
            proc = runner(
                ["secret-tool", "lookup", "service", service,
                 "account", account],
                capture_output=True, text=True, timeout=10,
            )
        else:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = proc.stdout.strip()
    return key if proc.returncode == 0 and key else None


def _security_stream_field_is_safe(value):
    return (
        isinstance(value, str)
        and bool(value)
        and not any(char in value for char in ('"', "\\", "\r", "\n"))
    )


def keychain_write(key, backend, runner, service, account):
    """Write a credential over stdin, never as a process argument."""
    if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
        return False
    try:
        if backend == "keychain":
            if (
                '"' in key
                or "\\" in key
                or not _security_stream_field_is_safe(service)
                or not _security_stream_field_is_safe(account)
            ):
                return False
            command = (
                f'add-generic-password -U -s "{service}" -a "{account}" '
                f'-l "Ambient API key" -w "{key}"\n'
            )
            proc = runner(
                ["security", "-i"], input=command,
                capture_output=True, text=True, timeout=15,
            )
        elif backend == "secret-tool":
            proc = runner(
                ["secret-tool", "store", "--label=Ambient API key",
                 "service", service, "account", account],
                input=key, capture_output=True, text=True, timeout=15,
            )
        else:
            return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def keychain_delete(backend, runner, service, account):
    """Delete one credential, treating absence as success where supported."""
    try:
        if backend == "keychain":
            proc = runner(
                ["security", "delete-generic-password", "-s", service,
                 "-a", account],
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode in (0, 44)
        if backend == "secret-tool":
            proc = runner(
                ["secret-tool", "clear", "service", service,
                 "account", account],
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode == 0
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def shared_key_env_is_set(shared_key_value):
    """Report a cross-install key value without ever adopting it."""
    return bool(shared_key_value)


def resolve_key_and_backend(conf, namespaced_key, keychain_reader, backend_value):
    """Resolve namespaced env, OS store, then this install's config value."""
    if namespaced_key:
        return namespaced_key, "env"
    if conf.get("AMBIENT_KEY_BACKEND") != "file":
        key = keychain_reader()
        if key:
            backend = backend_value() if callable(backend_value) else backend_value
            return key, (backend or "keychain")
    if conf.get("AMBIENT_API_KEY"):
        return conf["AMBIENT_API_KEY"], "file"
    return None, None


__all__ = (
    "secret_backend",
    "keychain_available",
    "keychain_read",
    "keychain_write",
    "keychain_delete",
    "shared_key_env_is_set",
    "resolve_key_and_backend",
)
