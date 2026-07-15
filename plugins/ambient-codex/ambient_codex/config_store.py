"""Defensive config parsing and reads with no import-time external effects."""

import contextlib
import os
import stat
import tempfile


def _parse_config_line(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip()


def parse_config_lines(lines):
    """Parse env-style lines, retaining the final value for duplicate keys."""
    pairs = (_parse_config_line(line) for line in lines)
    return {pair[0]: pair[1] for pair in pairs if pair is not None}


def _prepare_config_file(config_path, stderr, platform_name):
    try:
        file_stat = os.lstat(config_path)
        if not stat.S_ISREG(file_stat.st_mode):
            print(
                f"ambient: {config_path} is not a regular file — ignoring it",
                file=stderr,
            )
            return False
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            print(
                f"ambient: {config_path} is not owned by you — ignoring it",
                file=stderr,
            )
            return False
        mode_bits = file_stat.st_mode & 0o777
        if platform_name == "posix" and mode_bits != 0o600:
            os.chmod(config_path, 0o600)
            print(
                f"ambient: tightened {config_path} permissions "
                f"({oct(mode_bits)[2:]} -> 600)",
                file=stderr,
            )
        return True
    except FileNotFoundError:
        return False
    except OSError as err:
        print(f"ambient: cannot read {config_path}: {err}", file=stderr)
        return False


def read_config_file(config_path, launcher_name, stderr, platform_name):
    """Read a regular owner-controlled UTF-8 config file, or return empty."""
    if not _prepare_config_file(config_path, stderr, platform_name):
        return {}
    try:
        with open(config_path, encoding="utf-8") as config_file:
            return parse_config_lines(config_file)
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        print(
            f"ambient: {config_path} is corrupt (not valid UTF-8) — ignoring it. "
            f"Re-run: {launcher_name} setup",
            file=stderr,
        )
        return {}
    except OSError as err:
        print(f"ambient: cannot read {config_path}: {err}", file=stderr)
        return {}


def claim_state_dir(conf_dir, state_dir, state_marker, version):
    """Best-effort ownership marker creation for this install's state root."""
    if conf_dir != state_dir:
        return
    marker = os.path.join(conf_dir, state_marker)
    if os.path.exists(marker):
        return
    try:
        with open(marker, "w", encoding="utf-8") as marker_file:
            marker_file.write(f"ambient-codex {version}\n")
        os.chmod(marker, 0o600)
    except OSError:
        return


def private_dir(path):
    """Best-effort creation and healing of an owner-only state directory."""
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o700)
    except OSError:
        return


def _abort(abort, message):
    abort(message)
    raise RuntimeError(message)


@contextlib.contextmanager
def _posix_config_lock(lock_path, fcntl_module, abort):
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as err:
        _abort(abort, f"ambient: cannot open config lock: {err}")
    acquired = False
    try:
        try:
            fcntl_module.flock(descriptor, fcntl_module.LOCK_EX)
        except OSError as err:
            _abort(abort, f"ambient: cannot acquire config lock: {err}")
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl_module.flock(descriptor, fcntl_module.LOCK_UN)
        finally:
            os.close(descriptor)


def _portable_lock_is_contended(error, lock_path, platform_name=None):
    if isinstance(error, FileExistsError):
        return True
    if not isinstance(error, PermissionError):
        return False
    # Windows can keep a just-closed file in delete-pending state: O_EXCL then
    # raises PermissionError even though lexists() already reports false. Treat
    # that bounded transient exactly like an existing lock; the 10-second wait
    # still fails closed for a genuinely unwritable directory.
    if (platform_name or os.name) == "nt":
        return True
    try:
        return os.path.lexists(lock_path)
    except OSError:
        return False


def _wait_for_portable_lock(lock_path, abort, clock, sleeper, waited):
    try:
        if clock() - os.path.getmtime(lock_path) > 30:
            os.unlink(lock_path)
            return waited
    except OSError:
        pass
    if waited >= 10.0:
        _abort(
            abort,
            "ambient: config is locked by another ambient process "
            "(waited 10s). Retry in a moment.",
        )
    sleeper(0.1)
    return waited + 0.1


@contextlib.contextmanager
def _portable_config_lock(lock_path, abort, clock, sleeper):
    descriptor = None
    waited = 0.0
    while descriptor is None:
        try:
            descriptor = os.open(
                lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.write(descriptor, str(os.getpid()).encode())
        except OSError as err:
            if descriptor is None and _portable_lock_is_contended(err, lock_path):
                waited = _wait_for_portable_lock(
                    lock_path, abort, clock, sleeper, waited
                )
                continue
            if descriptor is not None:
                os.close(descriptor)
                descriptor = None
                try:
                    os.unlink(lock_path)
                except OSError:
                    pass
            _abort(abort, f"ambient: cannot open config lock: {err}")
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            os.unlink(lock_path)
        except OSError:
            pass


@contextlib.contextmanager
def config_lock(conf_dir, claim_state, fcntl_module, abort, clock, sleeper):
    """Acquire the existing POSIX or portable config-write lock."""
    claim_state()
    lock_path = os.path.join(conf_dir, ".env.lock")
    lock = (
        _posix_config_lock(lock_path, fcntl_module, abort)
        if fcntl_module is not None
        else _portable_config_lock(lock_path, abort, clock, sleeper)
    )
    with lock:
        yield


def _merge_config_lines(lines, updates):
    remaining = dict(updates)
    replaced = frozenset()
    merged = ()
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else None
        if key in remaining:
            value = remaining[key]
            remaining = {k: v for k, v in remaining.items() if k != key}
            replaced = replaced | frozenset((key,))
            if value is not None:
                merged = (*merged, f"{key}={value}")
        elif key in replaced or key in updates:
            continue
        else:
            merged = (*merged, line)
    additions = tuple(
        f"{key}={value}" for key, value in remaining.items() if value is not None
    )
    return (*merged, *additions)


def _atomic_write(config_path, conf_dir, lines, abort):
    try:
        descriptor, temp_path = tempfile.mkstemp(dir=conf_dir, prefix=".env.")
    except OSError as err:
        _abort(abort, f"ambient: failed to write {config_path}: {err}")
    descriptor_open = True
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        config_file = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor_open = False
        with config_file:
            config_file.write("\n".join(lines) + "\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temp_path, config_path)
    except OSError as err:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        _abort(abort, f"ambient: failed to write {config_path}: {err}")


def save_config_values(config_path, updates, lock_factory, abort):
    """Merge updates under lock and atomically replace an owner-only config."""
    conf_dir = os.path.dirname(config_path)
    try:
        os.makedirs(conf_dir, mode=0o700, exist_ok=True)
        if os.stat(conf_dir).st_mode & 0o077:
            os.chmod(conf_dir, 0o700)
    except OSError as err:
        _abort(abort, f"ambient: cannot create {conf_dir}: {err}")
    with lock_factory(conf_dir):
        try:
            with open(config_path, encoding="utf-8") as config_file:
                lines = tuple(config_file.read().splitlines())
        except FileNotFoundError:
            lines = ()
        except OSError as err:
            _abort(
                abort,
                f"ambient: cannot read config for update {config_path}: {err}",
            )
        resolved = updates(parse_config_lines(lines)) if callable(updates) else updates
        merged = _merge_config_lines(lines, dict(resolved))
        _atomic_write(config_path, conf_dir, merged, abort)
        try:
            os.chmod(config_path, 0o600)
        except OSError as err:
            _abort(abort, f"ambient: failed to secure {config_path}: {err}")


__all__ = (
    "parse_config_lines",
    "read_config_file",
    "claim_state_dir",
    "private_dir",
    "config_lock",
    "save_config_values",
)
