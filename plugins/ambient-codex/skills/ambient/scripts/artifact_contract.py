#!/usr/bin/env python3
"""Validate Ambient-authored artifact specifications and hash rendered outputs."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import tempfile


SUPPORTED_KINDS = frozenset(
    {"deck", "document", "site", "image_set", "campaign_bundle"}
)
CODEX_ROLES = frozenset(
    {"orchestration", "verification", "deterministic-rendering"}
)
EXTERNAL_PREFIXES = ("http://", "https://", "data:", "file:")
SOURCE_MEDIA_TYPES = frozenset(
    {
        "image/svg+xml",
        "text/css",
        "text/html",
        "text/javascript",
        "text/markdown",
        "text/plain",
    }
)


def _is_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_safe_relative_path(value):
    if not _is_string(value) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." != value


def prompt_sha256(value):
    """Hash exact prompt bytes; callers must not normalize encoding or newlines."""
    if not isinstance(value, bytes):
        raise TypeError("prompt_sha256 requires bytes")
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_generation_records(value, models, outputs):
    records = value.get("generation_records")
    if len(models) > 1 and not isinstance(records, list):
        return ("provenance.generation_records is required for multiple models",)
    if records is None:
        return ()
    if not isinstance(records, list) or not records:
        return ("provenance.generation_records must be a non-empty array",)
    issues, recorded_outputs = (), ()
    for index, record in enumerate(records):
        label = f"provenance.generation_records[{index}]"
        if not isinstance(record, dict):
            issues += (f"{label} must be an object",)
            continue
        if record.get("model") not in models:
            issues += (f"{label}.model must be declared in provenance.models",)
        if not _is_sha256(record.get("prompt_sha256")):
            issues += (f"{label}.prompt_sha256 must be a lowercase SHA-256",)
        paths = record.get("outputs")
        if not isinstance(paths, list) or not paths:
            issues += (f"{label}.outputs must be a non-empty array",)
        elif any(not _is_safe_relative_path(path) for path in paths):
            issues += (f"{label}.outputs must contain safe relative paths",)
        else:
            recorded_outputs += tuple(paths)
    if len(recorded_outputs) != len(set(recorded_outputs)):
        issues += ("provenance.generation_records outputs overlap",)
    if set(recorded_outputs) != set(outputs):
        issues += ("provenance.generation_records must exactly cover artifact outputs",)
    return issues


def _validate_provenance(value, outputs):
    if not isinstance(value, dict):
        return ("provenance must be an object",)
    issues = ()
    if value.get("creative_author") != "Ambient":
        issues += ("provenance.creative_author must equal Ambient",)
    if value.get("creative_authorship") != "ambient-only":
        issues += ("provenance.creative_authorship must equal ambient-only",)
    if not _is_string(value.get("model")):
        issues += ("provenance.model must be a non-empty string",)
    if not _is_sha256(value.get("prompt_sha256")):
        issues += ("provenance.prompt_sha256 must be a lowercase SHA-256",)
    roles = value.get("codex_roles")
    if not isinstance(roles, list) or not roles:
        issues += ("provenance.codex_roles must be a non-empty array",)
    elif len(roles) != len(set(roles)) or any(role not in CODEX_ROLES for role in roles):
        issues += ("provenance.codex_roles contains duplicate or unsupported roles",)
    models = value.get("models", [value.get("model")])
    if (
        not isinstance(models, list)
        or not models
        or len(models) != len(set(models))
        or any(not _is_string(model) for model in models)
        or value.get("model") not in models
    ):
        issues += ("provenance.models must be unique model ids including provenance.model",)
        return issues
    return issues + _validate_generation_records(value, tuple(models), outputs)


def _validate_rendering(value):
    if not isinstance(value, dict):
        return ("rendering must be an object",)
    issues = ()
    for field in ("engine", "engine_version", "font_policy", "asset_policy"):
        if not _is_string(value.get(field)):
            issues += (f"rendering.{field} must be a non-empty string",)
    return issues


def _validate_artifact_envelope(value):
    if not isinstance(value, dict):
        return ("artifact must be an object",)
    issues = ()
    if value.get("kind") not in SUPPORTED_KINDS:
        issues += ("artifact.kind is unsupported",)
    if not _is_string(value.get("title")):
        issues += ("artifact.title must be a non-empty string",)
    scenario = value.get("scenario")
    if not isinstance(scenario, dict) or not isinstance(scenario.get("fictional"), bool):
        issues += ("artifact.scenario.fictional must be boolean",)
    outputs = value.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return issues + ("artifact.outputs must be a non-empty array",)
    if len(outputs) != len(set(outputs)):
        issues += ("artifact.outputs contains a duplicate path",)
    if any(not _is_safe_relative_path(path) for path in outputs):
        issues += ("artifact.outputs entries must be safe relative POSIX paths",)
    return issues


def _validate_geometry(element, slide_width, slide_height, label, boundary="slide"):
    values = tuple(element.get(field) for field in ("x", "y", "width", "height"))
    issues = (
        (f"{label} geometry values must be finite numbers",)
        if any(not _is_number(value) for value in values)
        else ()
    )
    x, y, width, height = values
    if _is_number(width) and _is_number(height) and (width <= 0 or height <= 0):
        issues += (f"{label} width and height must be positive",)
    definitely_outside = (
        (_is_number(x) and x < 0)
        or (_is_number(y) and y < 0)
        or (_is_number(width) and width > slide_width)
        or (_is_number(height) and height > slide_height)
        or (all(_is_number(value) for value in (x, width)) and x + width > slide_width)
        or (all(_is_number(value) for value in (y, height)) and y + height > slide_height)
    )
    return issues + (
        (f"{label} geometry exceeds {boundary} bounds",) if definitely_outside else ()
    )


def _validate_element(element, slide_width, slide_height, label, boundary="slide"):
    if not isinstance(element, dict):
        return (f"{label} must be an object",)
    issues = _validate_geometry(element, slide_width, slide_height, label, boundary)
    element_type = element.get("type")
    if element_type not in {"text", "asset", "shape", "line"}:
        issues += (f"{label}.type is unsupported",)
    if element_type == "text" and not _is_string(element.get("text")):
        issues += (f"{label}.text must be non-empty",)
    if element_type == "asset":
        asset = element.get("asset")
        if isinstance(asset, str) and asset.lower().startswith(EXTERNAL_PREFIXES):
            issues += (f"{label}.asset must not reference an external resource",)
        elif not _is_safe_relative_path(asset):
            issues += (f"{label}.asset must be a safe relative path",)
    return issues


def _validate_slide(slide, slide_width, slide_height, index):
    label = f"content.slides[{index}]"
    if not isinstance(slide, dict):
        return (f"{label} must be an object",)
    issues = ()
    if not _is_string(slide.get("id")):
        issues += (f"{label}.id must be non-empty",)
    if not _is_string(slide.get("title")):
        issues += (f"{label}.title must be non-empty",)
    if not _is_string(slide.get("speaker_notes")):
        issues += (f"{label}.speaker_notes must be non-empty",)
    elements = slide.get("elements")
    if not isinstance(elements, list) or not elements:
        return issues + (f"{label}.elements must be a non-empty array",)
    return issues + sum(
        (
            _validate_element(
                element,
                slide_width,
                slide_height,
                f"{label}.elements[{element_index}]",
            )
            for element_index, element in enumerate(elements)
        ),
        (),
    )


def _validate_deck(content):
    if not isinstance(content, dict):
        return ("content must be an object",)
    slide_size = content.get("slide_size")
    if not isinstance(slide_size, dict):
        return ("content.slide_size must be an object",)
    width, height = slide_size.get("width"), slide_size.get("height")
    if not _is_number(width) or not _is_number(height) or width <= 0 or height <= 0:
        return ("content.slide_size width and height must be positive finite numbers",)
    slides = content.get("slides")
    if not isinstance(slides, list) or not slides:
        return ("content.slides must be a non-empty array",)
    ids = tuple(slide.get("id") for slide in slides if isinstance(slide, dict))
    duplicate_issue = (
        ("content.slides contains a duplicate slide id",)
        if len(ids) != len(set(ids))
        else ()
    )
    slide_issues = sum(
        (
            _validate_slide(slide, width, height, index)
            for index, slide in enumerate(slides)
        ),
        (),
    )
    return duplicate_issue + slide_issues


def _validate_page(page, page_width, page_height, index):
    label = f"content.pages[{index}]"
    if not isinstance(page, dict):
        return (f"{label} must be an object",)
    issues = ()
    if not _is_string(page.get("id")):
        issues += (f"{label}.id must be non-empty",)
    if not _is_string(page.get("title")):
        issues += (f"{label}.title must be non-empty",)
    elements = page.get("elements")
    if not isinstance(elements, list) or not elements:
        return issues + (f"{label}.elements must be a non-empty array",)
    for element_index, element in enumerate(elements):
        issues += _validate_element(
            element,
            page_width,
            page_height,
            f"{label}.elements[{element_index}]",
            "page",
        )
    return issues


def _validate_document(content):
    if not isinstance(content, dict):
        return ("content must be an object",)
    page_size = content.get("page_size")
    if not isinstance(page_size, dict):
        return ("content.page_size must be an object",)
    width, height = page_size.get("width"), page_size.get("height")
    if not _is_number(width) or not _is_number(height) or width <= 0 or height <= 0:
        return ("content.page_size width and height must be positive finite numbers",)
    pages = content.get("pages")
    if not isinstance(pages, list) or not pages:
        return ("content.pages must be a non-empty array",)
    ids = tuple(page.get("id") for page in pages if isinstance(page, dict))
    issues = (
        ("content.pages contains a duplicate page id",)
        if len(ids) != len(set(ids))
        else ()
    )
    for index, page in enumerate(pages):
        issues += _validate_page(page, width, height, index)
    return issues


def _validate_source_files(content, outputs, label, require_entrypoint=False):
    if not isinstance(content, dict):
        return (f"{label} must be an object",)
    files = content.get("files")
    if not isinstance(files, list) or not files:
        return (f"{label}.files must be a non-empty array",)
    issues, paths = (), ()
    for index, item in enumerate(files):
        item_label = f"{label}.files[{index}]"
        if not isinstance(item, dict):
            issues += (f"{item_label} must be an object",)
            continue
        path = item.get("path")
        if not _is_safe_relative_path(path):
            issues += (f"{item_label}.path must be a safe relative POSIX path",)
        else:
            paths += (path,)
        if item.get("media_type") not in SOURCE_MEDIA_TYPES:
            issues += (f"{item_label}.media_type is unsupported",)
        if not _is_string(item.get("source")):
            issues += (f"{item_label}.source must be non-empty",)
    if len(paths) != len(set(paths)):
        issues += (f"{label}.files contains a duplicate path",)
    if set(paths) != set(outputs):
        issues += (f"{label}.files must exactly match artifact outputs",)
    if require_entrypoint and content.get("entrypoint") not in paths:
        issues += (f"{label}.entrypoint must reference a declared file",)
    return issues


def _prefix_issues(issues, prefix):
    return tuple(f"{prefix}: {issue}" for issue in issues)


def _validate_bundle(content, outputs):
    if not isinstance(content, dict):
        return ("content must be an object",)
    artifacts = content.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ("content.artifacts must be a non-empty array",)
    issues, nested_outputs = (), ()
    for index, artifact in enumerate(artifacts):
        issues += _prefix_issues(validate_artifact(artifact), f"content.artifacts[{index}]")
        if isinstance(artifact, dict) and isinstance(artifact.get("artifact"), dict):
            child_outputs = artifact["artifact"].get("outputs")
            if isinstance(child_outputs, list):
                nested_outputs += tuple(child_outputs)
    if len(nested_outputs) != len(set(nested_outputs)):
        issues += ("content.artifacts contains duplicate output ownership",)
    if set(nested_outputs) != set(outputs):
        issues += ("artifact.outputs must exactly match nested artifact outputs",)
    return issues


def validate_artifact(document):
    """Return an immutable tuple of contract violations without changing input."""
    if not isinstance(document, dict):
        return ("root must be an object",)
    issues = ()
    if document.get("ambient_artifact_version") != 1:
        issues += ("ambient_artifact_version must equal 1",)
    artifact = document.get("artifact")
    issues += _validate_artifact_envelope(artifact)
    outputs = artifact.get("outputs", []) if isinstance(artifact, dict) else []
    issues += _validate_provenance(document.get("provenance"), outputs)
    issues += _validate_rendering(document.get("rendering"))
    kind = artifact.get("kind") if isinstance(artifact, dict) else None
    if kind == "deck":
        issues += _validate_deck(document.get("content"))
    elif kind == "site":
        issues += _validate_source_files(document.get("content"), outputs, "content", True)
    elif kind == "document":
        issues += _validate_document(document.get("content"))
    elif kind == "image_set":
        issues += _validate_source_files(document.get("content"), outputs, "content")
    elif kind == "campaign_bundle":
        issues += _validate_bundle(document.get("content"), outputs)
    elif not isinstance(document.get("content"), dict):
        issues += ("content must be an object",)
    return issues


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_document(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"unable to read artifact specification: {error}") from error
    issues = validate_artifact(value)
    if issues:
        raise ValueError("invalid artifact specification: " + "; ".join(issues))
    return value


def build_manifest(spec_path, root):
    """Return a deterministic provenance manifest for a valid rendered artifact."""
    spec_path, root = Path(spec_path).resolve(), Path(root).resolve()
    document = _load_document(spec_path)
    output_records = ()
    for relative in document["artifact"]["outputs"]:
        output = (root / relative).resolve()
        try:
            output.relative_to(root)
        except ValueError as error:
            raise ValueError(f"rendered output escapes root: {relative}") from error
        if not output.is_file() or output.is_symlink():
            raise ValueError(f"missing rendered output: {relative}")
        output_records += (
            {"path": relative, "sha256": _sha256(output), "bytes": output.stat().st_size},
        )
    provenance = document["provenance"]
    models = provenance.get("models", [provenance["model"]])
    return {
        "schema_version": 1,
        "creative_author": provenance["creative_author"],
        "creative_authorship": provenance["creative_authorship"],
        "model": provenance["model"],
        "models": list(models),
        "generation_records": list(provenance.get("generation_records", [])),
        "prompt_sha256": provenance["prompt_sha256"],
        "codex_roles": list(provenance["codex_roles"]),
        "artifact_kind": document["artifact"]["kind"],
        "fictional": document["artifact"]["scenario"]["fictional"],
        "rendering": dict(document["rendering"]),
        "spec_path": os.path.relpath(spec_path, root).replace(os.sep, "/"),
        "spec_sha256": _sha256(spec_path),
        "outputs": list(output_records),
    }


def _atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".artifact-manifest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("spec", type=Path)
    validate_parser.add_argument("--json", action="store_true")
    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("spec", type=Path)
    manifest_parser.add_argument("--root", required=True, type=Path)
    manifest_parser.add_argument("--output", required=True, type=Path)
    hash_parser = subparsers.add_parser("hash-prompt")
    hash_parser.add_argument("prompt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "hash-prompt":
            print(prompt_sha256(args.prompt.read_bytes()))
            return 0
        document = _load_document(args.spec)
        if args.command == "validate":
            payload = {
                "schema_version": 1,
                "status": "ok",
                "kind": document["artifact"]["kind"],
            }
            print(json.dumps(payload) if args.json else "Ambient artifact contract: valid")
            return 0
        manifest = build_manifest(args.spec, args.root)
        _atomic_write_json(args.output, manifest)
        print(str(args.output))
        return 0
    except ValueError as error:
        payload = {"schema_version": 1, "status": "invalid", "error": str(error)}
        print(json.dumps(payload) if getattr(args, "json", False) else f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
