"""One immutable Ambient build-generation request boundary."""

import argparse
import dataclasses


@dataclasses.dataclass(frozen=True)
class GenerationRequest:
    args: object
    api_key: str
    api_url: str
    model: str
    profile: object
    model_cpt: float
    single_shot_chars: int
    task: str
    context: str
    plan: tuple
    batch: tuple
    done_paths: tuple
    attempts: tuple
    recovery_paths: tuple
    project_brief: str
    session: object


@dataclasses.dataclass(frozen=True)
class GenerationReply:
    batch: tuple
    wanted_paths: tuple
    called: bool
    content: str
    body: object
    error: object
    prompt_too_large: bool


def generate_batch(request, deps):
    """Make one capacity-shaped model call without mutating build state."""
    batch = request.batch
    wanted = tuple(item["path"] for item in batch)
    prompt = deps._build_workflow.generation_prompt(
        task=request.task,
        batch=batch,
        plan=request.plan,
        done_paths=request.done_paths,
        context=request.context,
        system_chars=len(deps.BUILD_GEN_PROMPT),
        single_shot_chars=request.single_shot_chars,
        recovery_paths=request.recovery_paths,
        project_brief=request.project_brief,
    )
    if prompt is None:
        return GenerationReply(
            batch=batch,
            wanted_paths=wanted,
            called=False,
            content="",
            body={},
            error=None,
            prompt_too_large=True,
        )
    response_format = (
        deps.response_format_for(request.profile, deps.BUILD_FILES_SCHEMA)
        if len(batch) == 1
        and batch[0]["path"] not in request.recovery_paths
        else None
    )
    generation_args = argparse.Namespace(**vars(request.args))
    generation_args.response_format = response_format
    generation_chars = len(deps.BUILD_GEN_PROMPT) + len(prompt)
    generation_ceiling = deps._context_safe_escalation_ceiling(
        request.profile, generation_chars, request.model_cpt)
    if getattr(request.args, "_auto_budget", False):
        generation_args.max_tokens = None
        deps.apply_output_budget(
            generation_args, request.profile, generation_chars)
        desired_output = deps._build_workflow.generation_output_tokens(
            estimated_bytes=sum(
                deps._build_workflow.estimated_item_bytes(item)
                for item in batch),
            chars_per_token=request.model_cpt,
            is_reasoning=request.profile.is_reasoning,
            max_tokens=request.args.max_tokens,
        )
        generation_args.max_tokens = min(
            generation_ceiling,
            max(generation_args.max_tokens, desired_output),
        )
    else:
        generation_args.escalation_ceiling = max(
            generation_args.max_tokens, generation_ceiling)
    attempts = dict(request.attempts)
    generation_args.max_budget_escalations = (
        1 if len(batch) == 1
        and attempts.get(batch[0]["path"], 0) == 0
        else 0
    )
    try:
        content, _usage, body = deps.complete(
            request.api_key,
            request.api_url,
            request.model,
            [
                {"role": "system", "content": deps.BUILD_GEN_PROMPT},
                {"role": "user", "content": prompt},
            ],
            generation_args,
            session=request.session,
        )
        error = None
    except deps.ChatError as caught:
        content, body, error = "", {}, caught
    return GenerationReply(
        batch=batch,
        wanted_paths=wanted,
        called=True,
        content=content,
        body=body,
        error=error,
        prompt_too_large=False,
    )


__all__ = ("GenerationReply", "GenerationRequest", "generate_batch")
