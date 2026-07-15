"""Request isolation and final dispatch for focused code generation."""

import copy


def clone_request(args):
    """Return a shallow request copy so phase-local tuning cannot leak out."""
    return copy.copy(args)


def final_messages(task, context):
    """Return a fresh generation request with an optional context boundary."""
    user_content = task if not context else f"{task}\n\nContext files:\n\n{context}"
    return [
        {
            "role": "system",
            "content": (
                "You are a senior software engineer. Produce complete, correct, "
                "production-quality code. Include brief usage notes. No placeholders."
            ),
        },
        {"role": "user", "content": user_content},
    ]


def clamp_context(context, *, task, single_shot_chars):
    """Return a final-context value that leaves room for task and prompt text."""
    room = max(1_000, single_shot_chars - len(task) - 500)
    return (context if len(context) <= room
            else context[:room] + "\n[ambient: context truncated to fit]")


def dispatch_generation(api_key, api_url, model, task, context, args, *,
                        best_of_k, best_of_temperature, catalog, conf, session,
                        best_of_chat, chat):
    """Dispatch the final code request with generation-only sampling state."""
    request = clone_request(args)
    messages = final_messages(task, context)
    if best_of_k:
        request.temperature = best_of_temperature
        best_of_chat(
            api_key, api_url, model, messages, request, best_of_k,
            catalog, conf, kind="code", session=session)
        return
    chat(api_key, api_url, model, messages, request, kind="code",
         session=session)


__all__ = ("clone_request", "final_messages", "clamp_context",
           "dispatch_generation")
