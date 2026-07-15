"""Phase 3 contracts for individual map-item execution policy."""

import importlib
import unittest


class _ChatError(Exception):
    def __init__(self, category, diagnosis):
        super().__init__(diagnosis)
        self.category = category
        self.diagnosis = diagnosis
        self.retry_after = None


class MapWorkflowTests(unittest.TestCase):
    def test_clean_requested_model_result_is_cached(self):
        core = importlib.import_module("ambient_codex.map_workflow")
        cached = []
        out = core.run_map_item(
            item_id="one", text="input", key="cache-key", item_args=object(),
            prompt="inspect", api_key="key", api_url="url", model="requested",
            session=object(), gate=_NullContext(), cancel_event=_Event(),
            complete=lambda *_args, **_kwargs: ("answer", {}, {"_served_model": "requested"}),
            cache_put=lambda key, value: cached.append((key, value)),
            retry_delay=lambda *_args: 0, sleep=lambda _: None,
            chat_error=_ChatError, network_error=OSError, use_cache=True)
        self.assertEqual(out, ("answer", False))
        self.assertEqual(cached, [("cache-key", "answer")])

    def test_partial_or_fallback_result_never_poisons_requested_model_cache(self):
        core = importlib.import_module("ambient_codex.map_workflow")
        for body in ({"finish_reason": "length", "_served_model": "requested"},
                     {"_served_model": "fallback"}):
            cached = []
            _out, partial = core.run_map_item(
                item_id="one", text="input", key="cache-key", item_args=object(),
                prompt="inspect", api_key="key", api_url="url", model="requested",
                session=object(), gate=_NullContext(), cancel_event=_Event(),
                complete=lambda *_args, body=body, **_kwargs: ("answer", {}, body),
                cache_put=lambda key, value: cached.append((key, value)),
                retry_delay=lambda *_args: 0, sleep=lambda _: None,
                chat_error=_ChatError, network_error=OSError, use_cache=True)
            self.assertEqual(cached, [])
            self.assertEqual(partial, body.get("finish_reason") == "length")

    def test_rate_error_retries_but_cancelled_item_does_not_call_provider(self):
        core = importlib.import_module("ambient_codex.map_workflow")
        calls = []

        def complete(*_args, **_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise _ChatError("rate", "slow down")
            return "answer", {}, {"_served_model": "requested"}

        out = core.run_map_item(
            item_id="one", text="input", key="cache-key", item_args=object(),
            prompt="inspect", api_key="key", api_url="url", model="requested",
            session=object(), gate=_NullContext(), cancel_event=_Event(), complete=complete,
            cache_put=lambda *_: None, retry_delay=lambda *_: 0, sleep=lambda _: None,
            chat_error=_ChatError, network_error=OSError, use_cache=False)
        self.assertEqual(out, ("answer", False))
        self.assertEqual(len(calls), 2)
        cancelled = _Event(set=True)
        with self.assertRaises(_ChatError):
            core.run_map_item(
                item_id="one", text="input", key="cache-key", item_args=object(),
                prompt="inspect", api_key="key", api_url="url", model="requested",
                session=object(), gate=_NullContext(), cancel_event=cancelled,
                complete=lambda *_args, **_kwargs: self.fail("must not call provider"),
                cache_put=lambda *_: None, retry_delay=lambda *_: 0, sleep=lambda _: None,
                chat_error=_ChatError, network_error=OSError, use_cache=False)


class _Event:
    def __init__(self, set=False):
        self._set = set

    def is_set(self):
        return self._set

    def set(self):
        self._set = True


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
