"""Dependency-injected execution policy for one independent map item."""


def run_map_item(*, item_id, text, key, item_args, prompt, api_key, api_url,
                 model, session, gate, cancel_event, complete, cache_put,
                 retry_delay, sleep, chat_error, network_error, use_cache):
    """Run one map item with retry, cancellation, and cache-integrity rules."""
    if cancel_event.is_set():
        raise chat_error("cancelled", "map cancelled before this item started")
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": text}]
    for attempt in range(3):
        try:
            with gate:
                if cancel_event.is_set():
                    raise chat_error("cancelled", "map cancelled while waiting for a slot")
                try:
                    output, _usage, body = complete(
                        api_key, api_url, model, messages, item_args, session=session)
                except network_error:
                    cancel_event.set()
                    raise
                except chat_error as error:
                    if error.category in ("key", "funds"):
                        cancel_event.set()
                    raise
            partial = bool(body.get("salvaged_partial")) or body.get("finish_reason") == "length"
            if use_cache and not partial and body.get("_served_model", model) == model:
                cache_put(key, output)
            return output, partial
        except chat_error as error:
            if error.category == "rate" and attempt < 2 and not cancel_event.is_set():
                retry_after = getattr(error, "retry_after", None)
                sleep(retry_delay(3 * (attempt + 1),
                                  {"Retry-After": retry_after} if retry_after else None))
                continue
            raise
    raise AssertionError("map retry loop exhausted unexpectedly")


__all__ = ("run_map_item",)
