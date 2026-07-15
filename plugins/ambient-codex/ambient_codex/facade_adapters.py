"""Validated late-bound adapters for the extensionless CLI facade."""

import functools
import re


_NAME = re.compile(r"^[A-Za-z_]\w*$")


def _parse(specification):
    pairs = []
    for item in specification.split():
        public, separator, target = item.partition("=")
        target = target if separator else public
        if not _NAME.fullmatch(public) or not _NAME.fullmatch(target):
            raise ValueError(f"invalid facade adapter specification: {item!r}")
        pairs.append((public, target))
    return tuple(pairs)


def build(namespace, module_name, dependencies_name, specification):
    """Build adapters that resolve collaborators from ``namespace`` per call.

    Late resolution deliberately preserves the launcher's established testing
    and embedding seams: replacing a module, dependency builder, or any value
    read by that builder remains visible without rebuilding the adapter table.
    """
    adapters = []
    for public_name, target_name in _parse(specification):
        def adapter(*args, _target=target_name, **kwargs):
            module = namespace[module_name]
            dependencies = namespace[dependencies_name]()
            return getattr(module, _target)(*args, deps=dependencies, **kwargs)

        adapter = functools.update_wrapper(
            adapter, getattr(namespace[module_name], target_name))
        adapter.__name__ = public_name
        adapter.__qualname__ = public_name
        adapters.append(adapter)
    return tuple(adapters)


def install(namespace, module_name, dependencies_name, specification):
    """Build and install a group, returning its validated public names."""
    pairs = _parse(specification)
    adapters = build(
        namespace, module_name, dependencies_name, specification)
    if len(pairs) != len(adapters):
        raise RuntimeError("facade adapter cardinality mismatch")
    for (public_name, _target_name), adapter in zip(pairs, adapters):
        namespace[public_name] = adapter
    return tuple(public_name for public_name, _target_name in pairs)


def _resolve(namespace, path):
    parts = path.split(".")
    if not parts or any(not _NAME.fullmatch(part) for part in parts):
        raise ValueError(f"invalid facade dependency source: {path!r}")
    value = namespace[parts[0]]
    for part in parts[1:]:
        value = getattr(value, part)
    return value


def _parse_bindings(specification):
    pairs = []
    for item in specification.split():
        binding, separator, source = item.partition("=")
        source = source if separator else binding
        if not _NAME.fullmatch(binding):
            raise ValueError(f"invalid facade dependency binding: {item!r}")
        _resolve_path = source.split(".")
        if not _resolve_path or any(
                not _NAME.fullmatch(part) for part in _resolve_path):
            raise ValueError(f"invalid facade dependency source: {source!r}")
        pairs.append((binding, source))
    return tuple(pairs)


def bind(namespace, factory, specification):
    """Construct an immutable dependency object from late-bound facade names."""
    values = {
        binding: _resolve(namespace, source)
        for binding, source in _parse_bindings(specification)
    }
    return factory(**values)


__all__ = ("bind", "build", "install")
