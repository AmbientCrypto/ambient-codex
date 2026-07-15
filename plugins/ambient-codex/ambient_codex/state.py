"""Path and state-root validation without import-time external effects."""

import os


def resolve(path):
    """Normalize expansion, aliases, symlinks, case, and parent segments."""
    return os.path.normcase(
        os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    )


def is_within(child, parent):
    """Return whether child is parent or resolves beneath it, prefix-safely."""
    child, parent = resolve(child), resolve(parent)
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def foreign_root(path, roots):
    """Return the explicitly supplied foreign root containing path, if any."""
    for root in roots:
        if (os.path.exists(root) or os.path.isabs(root)) and is_within(path, root):
            return root
    return None


def state_root_error(root, foreign_roots, state_marker, home_env_name):
    """Return the existing state-root refusal message, or None when accepted."""
    owner = foreign_root(root, foreign_roots)
    if owner is not None:
        return (
            f"ambient: refusing to use {root} as a state root — it is inside {owner}, "
            f"which belongs to another Ambient install. Unset {home_env_name}, "
            "or point it at a directory of your own."
        )
    if (
        os.path.exists(os.path.join(root, "env"))
        and not os.path.exists(os.path.join(root, state_marker))
    ):
        return (
            f"ambient: refusing to use {root} as a state root — it already holds an "
            f"Ambient config this install did not create (no {state_marker} marker). "
            f"Point {home_env_name} at an empty directory."
        )
    return None


__all__ = ("resolve", "is_within", "foreign_root", "state_root_error")
