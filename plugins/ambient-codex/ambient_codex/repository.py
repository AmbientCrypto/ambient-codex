"""Repository text transforms and bounded size accounting."""

import concurrent.futures
import os
import stat
import subprocess
from typing import NamedTuple


_READ_BLOCK_BYTES = 1 << 16
_BINARY_SNIFF_BYTES = 8_192
_GIT_LIST_TIMEOUT_SECONDS = 30
_OVERSIZE_PATH_LIMIT = 40
_GIT_META_BYTES = 65_536
_GIT_STDERR_BYTES = 4_096
_GIT_REF_MAX_CHARS = 4_096
_GIT_TERMINATE_WAIT_SECONDS = 1
_GIT_CANDIDATE_BYTES = 20_000_000
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
_BLOCKED_GIT_ENV = frozenset({
    "GIT_EXTERNAL_DIFF",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
})
_GIT_COMMAND_PREFIX = ("git", "-c", "core.fsmonitor=false", "--no-pager")


class RepositorySkips(NamedTuple):
    """Immutable repository omission counters and bounded path evidence."""

    binary: int = 0
    lockfile: int = 0
    oversize: int = 0
    oversize_paths: tuple = ()
    nonregular: int = 0
    vendored: int = 0


class GitDiffSnapshot(NamedTuple):
    """Immutable complete Git diff plus safe current-file path coverage."""

    diff_text: str
    root: str
    changed_files: tuple
    omitted_paths: tuple


class GitDiffFailure(NamedTuple):
    """Immutable lower-layer failure for facade error/exit orchestration."""

    category: str
    message: str
    usage: bool = False


class _PipeCapture(NamedTuple):
    data: bytes = b""
    overflow: bool = False
    error: object = None


class _GitCommand(NamedTuple):
    returncode: int = -1
    stdout: bytes = b""
    stderr: bytes = b""
    overflow: bool = False
    overflow_cap: int = 0
    overflow_stream: str = ""
    timed_out: bool = False
    launch_error: object = None
    read_error: object = None


def _terminate_process(process):
    try:
        process.terminate()
    except (OSError, ValueError):
        pass


def _kill_after_wait(process, timeout_error):
    _terminate_process(process)
    try:
        process.wait(timeout=_GIT_TERMINATE_WAIT_SECONDS)
        return
    except timeout_error:
        pass
    except (OSError, ValueError):
        return
    try:
        process.kill()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=_GIT_TERMINATE_WAIT_SECONDS)
    except (OSError, ValueError, timeout_error):
        pass


def _read_bounded_pipe(stream, limit, stop):
    chunks = ()
    total = 0
    overflow = False
    error = None
    try:
        while True:
            block = stream.read(min(_READ_BLOCK_BYTES, limit - total + 1))
            if not block:
                break
            if not isinstance(block, bytes):
                error = "subprocess pipe returned non-bytes data"
                stop()
                break
            room = limit - total
            if len(block) > room:
                if room > 0:
                    chunks = (*chunks, block[:room])
                overflow = True
                stop()
                break
            chunks = (*chunks, block)
            total += len(block)
    except (OSError, ValueError) as err:
        error = str(err)
        stop()
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    return _PipeCapture(b"".join(chunks), overflow, error)


def _wait_for_git(process, timeout_error, timeout_seconds):
    try:
        return process.wait(timeout=timeout_seconds), False, None
    except timeout_error:
        _kill_after_wait(process, timeout_error)
        return (
            process.returncode if process.returncode is not None else -1,
            True,
            None,
        )
    except (OSError, ValueError) as err:
        _kill_after_wait(process, timeout_error)
        return (
            process.returncode if process.returncode is not None else -1,
            False,
            str(err),
        )


def _pipe_results(process, timeout_error, stdout_future, stderr_future):
    try:
        return stdout_future.result(), stderr_future.result(), None
    except Exception as err:
        _kill_after_wait(process, timeout_error)
        return _PipeCapture(), _PipeCapture(), str(err)


def _git_environment():
    retained = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in _BLOCKED_GIT_ENV
        and not name.upper().startswith("GIT_CONFIG_KEY_")
        and not name.upper().startswith("GIT_CONFIG_VALUE_")
        and not any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
    }
    return {
        **retained,
        "GIT_PAGER": "",
        "PAGER": "",
        "GIT_ATTR_NOSYSTEM": "1",
    }


def _git_command(*arguments):
    return [*_GIT_COMMAND_PREFIX, *arguments]


def _run_bounded_git(
        command, popen, timeout_error, stdout_cap, stderr_cap, timeout_seconds
):
    try:
        process = popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except (OSError, ValueError) as err:
        return _GitCommand(launch_error=str(err))
    if process.stdout is None or process.stderr is None:
        _kill_after_wait(process, timeout_error)
        return _GitCommand(read_error="subprocess did not expose output pipes")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        stdout_future = pool.submit(
            _read_bounded_pipe, process.stdout, stdout_cap,
            lambda: _kill_after_wait(process, timeout_error),
        )
        stderr_future = pool.submit(
            _read_bounded_pipe, process.stderr, stderr_cap,
            lambda: _kill_after_wait(process, timeout_error),
        )
        returncode, timed_out, wait_error = _wait_for_git(
            process, timeout_error, timeout_seconds
        )
        stdout, stderr, future_error = _pipe_results(
            process, timeout_error, stdout_future, stderr_future
        )
        if future_error is not None:
            return _GitCommand(read_error=future_error)
    return _GitCommand(
        returncode=returncode,
        stdout=stdout.data,
        stderr=stderr.data,
        overflow=stdout.overflow or stderr.overflow,
        overflow_cap=(stdout_cap if stdout.overflow else stderr_cap),
        overflow_stream=("stdout" if stdout.overflow else "stderr"),
        timed_out=timed_out,
        read_error=wait_error or stdout.error or stderr.error,
    )


def _entry_kind(entry, skip_dirs):
    try:
        if entry.is_dir(follow_symlinks=False):
            if entry.name in skip_dirs or entry.name.startswith("."):
                return "skip"
            return "directory"
        if entry.is_symlink() and entry.is_dir():
            return "skip"
    except OSError:
        return "file"
    return "file"


def _scan_directory(directory, skip_dirs):
    try:
        with os.scandir(directory) as scanner:
            entries = tuple(sorted(tuple(scanner), key=lambda entry: entry.name))
    except OSError:
        return (), ()
    classified = tuple(
        (_entry_kind(entry, skip_dirs), entry.path) for entry in entries
    )
    directories = tuple(path for kind, path in classified if kind == "directory")
    files = tuple(path for kind, path in classified if kind == "file")
    return directories, files


def _plain_candidate_paths(root, skip_dirs):
    pending = (root, None)
    while pending is not None:
        directory, pending = pending
        children, files = _scan_directory(directory, skip_dirs)
        for path in files:
            yield os.path.relpath(path, root)
        for child in reversed(children):
            pending = (child, pending)


def _decode_git_paths(output):
    if isinstance(output, bytes):
        values = output.split(b"\0")
    elif isinstance(output, str):
        values = output.split("\0")
    else:
        return None
    try:
        return tuple(os.fsdecode(value) for value in values if value)
    except (UnicodeError, ValueError):
        return None


def candidate_paths(
        root, run_git, timeout_error, skip_dirs, popen=None,
        byte_cap=_GIT_CANDIDATE_BYTES,
):
    """Return immutable Git-aware or plain-walk repository candidates."""
    root = os.fsdecode(os.fspath(root))
    skip_dirs = frozenset(skip_dirs)
    command = _git_command(
        "-C", root, "ls-files", "-z", "--cached", "--others",
        "--exclude-standard",
    )
    if popen is None:
        try:
            completed = run_git(
                command,
                capture_output=True,
                timeout=_GIT_LIST_TIMEOUT_SECONDS,
                env=_git_environment(),
            )
        except (OSError, ValueError, timeout_error):
            completed = None
    else:
        if isinstance(byte_cap, bool) or not isinstance(byte_cap, int) or byte_cap <= 0:
            raise ValueError("candidate-path ceiling must be a positive integer")
        result = _run_bounded_git(
            command, popen, timeout_error, byte_cap, _GIT_STDERR_BYTES,
            _GIT_LIST_TIMEOUT_SECONDS,
        )
        completed = None if (
            result.launch_error is not None
            or result.read_error is not None
            or result.timed_out
            or result.overflow
        ) else result
    if completed is not None and getattr(completed, "returncode", 1) == 0:
        paths = _decode_git_paths(getattr(completed, "stdout", None))
        if paths is not None:
            return paths, True
    return tuple(_plain_candidate_paths(root, skip_dirs)), False


def _candidate_text(candidate):
    try:
        return os.fsdecode(os.fspath(candidate))
    except (TypeError, UnicodeError, ValueError):
        return None


def _candidate_contract(candidate, used_git, skip_dirs, lockfiles):
    relative = _candidate_text(candidate)
    if relative is None:
        return None, None, None
    portable = relative.replace(os.sep, "/")
    parts = portable.split("/")
    if not relative or "\0" in relative or os.path.isabs(relative) or ".." in parts:
        return None, None, None
    if any(
            part in skip_dirs
            or (not used_git and part != "." and part.startswith("."))
            for part in parts[:-1]
    ):
        return None, None, "vendored"
    if parts[-1] in lockfiles or parts[-1].endswith(".lock"):
        return None, None, "lockfile"
    return relative, relative.replace("\\", "/"), None


def _within_root(child_real, root_real):
    try:
        return os.path.commonpath([child_real, root_real]) == root_real
    except ValueError:
        return False


def _close_descriptor(descriptor):
    try:
        os.close(descriptor)
    except OSError:
        pass


def _open_matching_descriptor(path, expected_stat):
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, None, False
    try:
        opened_stat = os.fstat(descriptor)
    except OSError:
        _close_descriptor(descriptor)
        return None, None, False
    try:
        matches = os.path.samestat(expected_stat, opened_stat)
    except (AttributeError, OSError, ValueError):
        matches = False
    if not stat.S_ISREG(opened_stat.st_mode) or not matches:
        _close_descriptor(descriptor)
        return None, None, True
    return descriptor, opened_stat, False


def _inspect_candidate(root, root_real, relative, label, per_file_cap):
    full = os.path.join(root, relative)
    try:
        expected_stat = os.lstat(full)
    except (OSError, ValueError):
        return None, None, label
    if not stat.S_ISREG(expected_stat.st_mode):
        return None, "nonregular", label
    if not _within_root(os.path.realpath(full), root_real):
        return None, "nonregular", label
    descriptor, opened_stat, mismatch = _open_matching_descriptor(full, expected_stat)
    if descriptor is None:
        return None, "nonregular" if mismatch else None, label
    try:
        if not _within_root(os.path.realpath(full), root_real):
            return None, "nonregular", label
        if opened_stat.st_size == 0:
            return None, None, label
        if opened_stat.st_size > per_file_cap:
            return None, "oversize", label
        head = os.read(descriptor, _BINARY_SNIFF_BYTES)
    except (OSError, ValueError):
        return None, None, label
    finally:
        _close_descriptor(descriptor)
    if not isinstance(head, bytes):
        return None, None, label
    if b"\0" in head:
        return None, "binary", label
    return (label, full, opened_stat.st_size), None, label


def _classify_candidate(
        root, root_real, candidate, used_git, per_file_cap, skip_dirs, lockfiles
):
    relative, label, reason = _candidate_contract(
        candidate, used_git, skip_dirs, lockfiles
    )
    if relative is None:
        return None, reason, label
    return _inspect_candidate(root, root_real, relative, label, per_file_cap)


def _record_skip(skipped, reason, label):
    if reason == "binary":
        return skipped._replace(binary=skipped.binary + 1)
    if reason == "lockfile":
        return skipped._replace(lockfile=skipped.lockfile + 1)
    if reason == "nonregular":
        return skipped._replace(nonregular=skipped.nonregular + 1)
    if reason == "vendored":
        return skipped._replace(vendored=skipped.vendored + 1)
    if reason == "oversize":
        paths = skipped.oversize_paths
        if label is not None and len(paths) < _OVERSIZE_PATH_LIMIT:
            paths = (*paths, label)
        return skipped._replace(oversize=skipped.oversize + 1, oversize_paths=paths)
    return skipped


def classify_repository_files(
        root, candidates, used_git, per_file_cap, skip_dirs, lockfiles
):
    """Return immutable auditable files and bounded omission metadata."""
    if (
        isinstance(per_file_cap, bool)
        or not isinstance(per_file_cap, int)
        or per_file_cap <= 0
    ):
        raise ValueError("per-file ceiling must be a positive integer")
    root = os.fsdecode(os.fspath(root))
    root_real = os.path.realpath(root)
    skip_dirs = frozenset(skip_dirs)
    lockfiles = frozenset(lockfiles)
    normalized = tuple(
        value for value in (_candidate_text(item) for item in candidates)
        if value is not None
    )
    outcomes = tuple(
        _classify_candidate(
            root, root_real, candidate, used_git, per_file_cap,
            skip_dirs, lockfiles,
        )
        for candidate in sorted(set(normalized))
    )
    files = tuple(item for item, _reason, _label in outcomes if item is not None)
    skipped = RepositorySkips()
    for _item, reason, label in outcomes:
        skipped = _record_skip(skipped, reason, label)
    return files, skipped


def _safe_message(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    else:
        value = str(value)
    escaped = "".join(
        character if character.isprintable() else "\\x{0:02x}".format(ord(character))
        for character in value.strip()
    )
    return escaped[:300]


def _runtime_failure(result, label, byte_cap):
    if result.launch_error is not None:
        return GitDiffFailure(
            "input", "unable to run git: " + _safe_message(result.launch_error)
        )
    if result.timed_out:
        return GitDiffFailure("input", f"{label} timed out after 30 seconds")
    if result.read_error is not None:
        return GitDiffFailure(
            "input", "cannot read git output: " + _safe_message(result.read_error)
        )
    if result.overflow:
        overflow_cap = getattr(result, "overflow_cap", 0) or byte_cap
        stream = getattr(result, "overflow_stream", "")
        stream_label = f" {stream}" if stream else ""
        return GitDiffFailure(
            "input",
            f"{label}{stream_label} exceeds the {overflow_cap:,}-byte safety "
            "ceiling; narrow "
            "the revision range or audit smaller changes.",
        )
    return None


def _capture_command(
        command, label, byte_cap, popen, timeout_error
):
    result = _run_bounded_git(
        command,
        popen,
        timeout_error,
        byte_cap,
        _GIT_STDERR_BYTES,
        _GIT_LIST_TIMEOUT_SECONDS,
    )
    return result, _runtime_failure(result, label, byte_cap)


def _validated_revision(staged, ref):
    if staged:
        return None
    if not isinstance(ref, str) or not ref or len(ref) > _GIT_REF_MAX_CHARS:
        return GitDiffFailure("usage", "--diff revision must be a nonempty string", True)
    if ref.startswith("-"):
        return GitDiffFailure(
            "usage", "--diff revision cannot start with '-' (use --staged for the index)",
            True,
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in ref):
        return GitDiffFailure(
            "usage", "--diff revision cannot contain NUL or control characters", True
        )
    return None


def _decode_root(output):
    if not isinstance(output, bytes):
        return None
    if output.endswith(b"\n"):
        output = output[:-1]
        if output.endswith(b"\r"):
            output = output[:-1]
    try:
        return os.fsdecode(output) if output else None
    except (UnicodeError, ValueError):
        return None


def _changed_path_outcome(root_real, path):
    relative = _candidate_text(path)
    if relative is None:
        return None, None
    label = relative.replace("\\", "/")
    portable = relative.replace(os.sep, "/")
    parts = portable.split("/")
    if not relative or "\0" in relative or os.path.isabs(relative) or ".." in parts:
        return None, label
    full = os.path.join(root_real, relative)
    try:
        contained = _within_root(os.path.realpath(full), root_real)
    except (OSError, ValueError):
        contained = False
    if not contained:
        return None, label
    return (label, full), None


def _changed_file_snapshot(root, paths):
    root_real = os.path.realpath(root)
    outcomes = tuple(_changed_path_outcome(root_real, path) for path in paths)
    changed = tuple(item for item, _omitted in outcomes if item is not None)
    omitted = tuple(
        path for _item, path in outcomes if path is not None
    )[:_OVERSIZE_PATH_LIMIT]
    return root_real, changed, omitted


def _capture_repository_check(popen, timeout_error):
    result, failure = _capture_command(
        _git_command("rev-parse", "--is-inside-work-tree"),
        "git repository check", _GIT_META_BYTES, popen, timeout_error,
    )
    if failure is not None:
        return failure
    if result.returncode != 0 or result.stdout.strip() != b"true":
        return GitDiffFailure(
            "usage", "--staged/--diff must run inside a git repository.", True
        )
    return None


def _capture_diff_text(staged, ref, popen, timeout_error, char_cap):
    diff_options = ("--no-ext-diff", "--no-textconv")
    arguments = (
        _git_command("diff", *diff_options, "--cached", "--")
        if staged else _git_command("diff", *diff_options, ref, "--")
    )
    result, failure = _capture_command(
        arguments, "git diff", char_cap, popen, timeout_error
    )
    if failure is not None:
        return None, failure
    if result.returncode != 0:
        return None, GitDiffFailure(
            "input", "git diff failed: " + _safe_message(result.stderr)
        )
    diff_text = result.stdout.decode("utf-8", errors="replace")
    if not diff_text.strip():
        return None, GitDiffFailure(
            "usage", "no changes to audit (empty diff).", True
        )
    return diff_text, None


def _capture_repo_root(popen, timeout_error):
    result, failure = _capture_command(
        _git_command("rev-parse", "--show-toplevel"),
        "git rev-parse --show-toplevel", _GIT_META_BYTES, popen, timeout_error,
    )
    if failure is not None:
        return None, failure
    root = _decode_root(result.stdout)
    if result.returncode != 0 or root is None:
        return None, GitDiffFailure(
            "input", "git rev-parse --show-toplevel failed: "
            + _safe_message(result.stderr)
        )
    return root, None


def _capture_changed_paths(staged, ref, popen, timeout_error, char_cap):
    diff_options = ("--no-ext-diff", "--no-textconv", "--name-only", "-z")
    arguments = (
        _git_command("diff", *diff_options, "--cached", "--")
        if staged else _git_command("diff", *diff_options, ref, "--")
    )
    result, failure = _capture_command(
        arguments, "git changed-path listing", char_cap, popen, timeout_error
    )
    if failure is not None:
        return None, failure
    paths = _decode_git_paths(result.stdout)
    if result.returncode != 0 or paths is None:
        return None, GitDiffFailure(
            "input", "git changed-path listing failed: "
            + _safe_message(result.stderr)
        )
    return paths, None


def capture_git_diff(staged, ref, popen, timeout_error, char_cap):
    """Return a complete bounded Git diff snapshot or explicit failure."""
    if isinstance(char_cap, bool) or not isinstance(char_cap, int) or char_cap <= 0:
        raise ValueError("Git diff ceiling must be a positive integer")
    failure = _validated_revision(staged, ref)
    if failure is not None:
        return None, failure
    failure = _capture_repository_check(popen, timeout_error)
    if failure is not None:
        return None, failure
    diff_text, failure = _capture_diff_text(
        staged, ref, popen, timeout_error, char_cap
    )
    if failure is not None:
        return None, failure
    root, failure = _capture_repo_root(popen, timeout_error)
    if failure is not None:
        return None, failure
    paths, failure = _capture_changed_paths(
        staged, ref, popen, timeout_error, char_cap
    )
    if failure is not None:
        return None, failure
    root_real, changed, omitted = _changed_file_snapshot(root, paths)
    return GitDiffSnapshot(diff_text, root_real, changed, omitted), None


def with_line_gutters(labeled):
    """Return immutable labeled text with absolute one-based line prefixes."""
    output = ()
    for label, text in labeled:
        lines = text.split("\n")
        width = max(2, len(str(len(lines))))
        guttered = "\n".join(
            f"{index:>{width}}| {line}"
            for index, line in enumerate(lines, 1)
        )
        output = (*output, (label, guttered))
    return output


def _open_regular_descriptor(path):
    try:
        path_stat = os.lstat(path)
    except OSError as err:
        return None, str(err)
    if not stat.S_ISREG(path_stat.st_mode):
        return None, "not a regular file"
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as err:
        return None, str(err)
    try:
        opened_stat = os.fstat(descriptor)
    except OSError as err:
        os.close(descriptor)
        return None, str(err)
    if not stat.S_ISREG(opened_stat.st_mode):
        os.close(descriptor)
        return None, "not a regular file"
    return descriptor, None


def guttered_file_size(path, size):
    """Conservatively estimate post-gutter characters with bounded file I/O."""
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("snapshot size must be a non-negative integer")
    descriptor, error = _open_regular_descriptor(path)
    if error is not None:
        return size
    remaining = size + 1
    bytes_read = 0
    newline_count = 0
    try:
        while remaining > 0:
            block = os.read(descriptor, min(_READ_BLOCK_BYTES, remaining))
            if not block:
                break
            if not isinstance(block, bytes):
                return size
            bytes_read += len(block)
            remaining -= len(block)
            newline_count += block.count(b"\n")
    except OSError:
        return size
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    lines = newline_count + 1
    width = max(2, len(str(lines)))
    return max(size, bytes_read) + lines * (width + 2)


__all__ = (
    "RepositorySkips",
    "GitDiffSnapshot",
    "GitDiffFailure",
    "candidate_paths",
    "classify_repository_files",
    "capture_git_diff",
    "with_line_gutters",
    "guttered_file_size",
)
