"""Filesystem transaction boundary for approved Ambient build records."""

import hashlib
import os
import tempfile


def _parent_is_safe(destination, root, within_root):
    root_real = os.path.realpath(root)
    parent_real = os.path.realpath(os.path.dirname(destination))
    return within_root(parent_real, root_real)


def _matches(destination, digest):
    if not os.path.isfile(destination) or os.path.islink(destination):
        return False
    try:
        with open(destination, "rb") as handle:
            current = handle.read()
    except OSError:
        return False
    return hashlib.sha256(current).hexdigest() == digest


def _write_record(destination, content, root, within_root):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if not _parent_is_safe(destination, root, within_root):
        raise OSError("parent escaped the target directory")
    descriptor, temporary = tempfile.mkstemp(
        dir=os.path.dirname(destination), prefix=".ambient-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        if os.path.islink(destination):
            raise OSError("destination became a symlink")
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def apply_records(done, root, *, force, backup_stamp, within_root):
    """Apply approved records and return immutable action/failure tuples."""
    actions, failures = [], []
    backup_root = os.path.join(root, ".ambient-build.bak", backup_stamp)
    for relative, record in sorted(done.items()):
        destination = os.path.join(root, *relative.split("/"))
        if not _parent_is_safe(destination, root, within_root):
            actions.append((relative, "write-failed"))
            failures.append(
                (relative, "parent escaped the target directory before write"))
            continue
        action = "create"
        if os.path.lexists(destination):
            if _matches(destination, record["sha256"]):
                actions.append((relative, "unchanged"))
                continue
            if not force:
                actions.append((relative, "skip-exists"))
                continue
            action = "overwrite"
            try:
                backup = os.path.join(backup_root, *relative.split("/"))
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                os.replace(destination, backup)
            except OSError as error:
                actions.append((relative, "write-failed"))
                failures.append((
                    relative,
                    "backup failed, refused to overwrite without a backup: "
                    f"{error}",
                ))
                continue
        try:
            _write_record(destination, record["content"], root, within_root)
            actions.append((relative, action))
        except OSError as error:
            actions.append((relative, "write-failed"))
            failures.append((relative, f"write failed: {error}"))
    return tuple(actions), tuple(failures)


__all__ = ("apply_records",)
