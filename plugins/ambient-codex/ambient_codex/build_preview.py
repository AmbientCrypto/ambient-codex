"""Zero-egress Ambient build cost and capacity preview."""

import dataclasses


@dataclasses.dataclass(frozen=True)
class BuildPreview:
    args: object
    catalog: tuple
    model: str
    reduce_model: str
    task: str
    context: str
    root: str
    profile: object
    needs_distill: bool
    packed: tuple
    single_shot_chars: int
    target_file_bytes: int
    dry_run_auto_model: object
    conf: object


def emit_build_preview(preview, deps):
    """Print the same bounded cost shape the live generation lane will use."""
    args = preview.args
    generation_calls = max(2, args.max_files // 4 + 2)
    generation_overhead = 2_000
    if preview.needs_distill:
        generation_context = min(
            len(preview.context), preview.single_shot_chars // 2)
        generation_chars = (
            len(preview.task) + generation_context + generation_overhead)
        expected, bound, assumed = deps.estimate_cost_mr_fb(
            preview.catalog,
            preview.model,
            preview.reduce_model,
            len(preview.context),
            len(preview.packed),
            args.max_tokens,
            args,
            preview.conf,
            extra_calls=1,
            per_call_chars=[len(chunk) for chunk in preview.packed],
        )
        extra_expected, extra_bound, extra_assumed = deps.estimate_cost_fb(
            preview.catalog,
            preview.model,
            generation_chars,
            generation_calls - 1,
            args.max_tokens,
            args,
            preview.conf,
            per_call_chars=[generation_chars] * (generation_calls - 1),
        )
        expected += extra_expected
        bound += extra_bound
        assumed = assumed or extra_assumed
    else:
        generation_chars = (
            len(preview.task) + len(preview.context) + generation_overhead)
        expected, bound, assumed = deps.estimate_cost_fb(
            preview.catalog,
            preview.model,
            generation_chars,
            generation_calls,
            args.max_tokens,
            args,
            preview.conf,
            per_call_chars=[generation_chars] * generation_calls,
        )
    # Cost helpers enforce the price gate during live execution. Dry-run keeps
    # their calculation for parity even though its user contract is descriptive.
    _cost_shape = expected, bound, assumed
    print("ambient-codex build plan (dry run — nothing sent):")
    if preview.dry_run_auto_model:
        print(
            f"  model:         {preview.dry_run_auto_model} "
            f"(live selection deferred; sizing preview uses {preview.model})"
        )
    else:
        lane = "reasoning" if preview.profile.is_reasoning else "direct"
        print(f"  model:         {preview.model} ({lane})")
    if preview.needs_distill and preview.reduce_model != preview.model:
        print(
            f"  models:        map={preview.model}, "
            f"reduce={preview.reduce_model}"
        )
    context_note = (
        f" + context {len(preview.context):,} chars"
        if preview.context else ""
    )
    print(f"  task:          {len(preview.task):,} chars{context_note}")
    if preview.needs_distill:
        print(
            "  note:          context exceeds half the window — a live "
            "run distills it first (extra billed calls)"
        )
    print(f"  output budget: {args.max_tokens:,} tokens/call")
    write_mode = "writes ON (--apply)" if args.apply else "manifest only"
    print(f"  target dir:    {preview.root} ({write_mode})")
    print(
        f"  caps:          ≤{args.max_files} files, "
        f"≤{args.max_file_bytes:,} bytes/file"
    )
    print(
        f"  artifact plan: target ≤{preview.target_file_bytes:,} estimated "
        "bytes/file (larger artifacts must be decomposed)"
    )
    print(
        f"  generation:    dependency waves, up to "
        f"{deps._resolve_parallel(args)} independent workstreams in parallel"
    )


__all__ = ("BuildPreview", "emit_build_preview")
