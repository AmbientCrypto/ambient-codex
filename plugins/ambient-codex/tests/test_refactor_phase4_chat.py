"""Phase 4 contracts for pure interactive-chat workflow policies."""

import importlib
from types import SimpleNamespace
import unittest


class ChatWorkflowTests(unittest.TestCase):
    def test_trim_history_is_immutable_and_retains_the_latest_exchange(self):
        core = importlib.import_module("ambient_codex.chat_workflow")
        history = [{"content": "old"}, {"content": "reply"},
                   {"content": "new"}, {"content": "latest"}]
        self.assertEqual(core.trim_history(history, 10), history[-2:])
        self.assertEqual(history[0]["content"], "old")

    def test_single_shot_streams_redacts_and_preserves_served_model(self):
        core = importlib.import_module("ambient_codex.chat_workflow")
        writes = []
        rendered = []

        class Output:
            def isatty(self):
                return True

            def write(self, value):
                writes.append(value)

            def flush(self):
                pass

        class Redactor:
            def __init__(self, _secret):
                pass

            def feed(self, value):
                return value.replace("sk-test", "[redacted]")

            def flush(self):
                return ""

        spec = SimpleNamespace(json=False, raw=False, allow_partial=False)
        session = SimpleNamespace(api_key="sk-test", api_url="https://api.example")

        core.single_shot_response(
            "unused", "unused", "requested/model", [{"role": "user", "content": "hi"}],
            object(), kind="ask", session=session,
            session_or=lambda supplied, *_args: supplied,
            request_spec=lambda _args: spec,
            stdout=Output(), stream_redactor=Redactor,
            complete=lambda *_args, **kwargs: (
                kwargs["on_delta"]("sk-test hi") or
                ("sk-test hi", {"prompt_tokens": 1}, {"_served_model": "served/model"})
            ),
            failure=lambda *_args: self.fail("unexpected completion failure"),
            completion_error=RuntimeError,
            emit_json=lambda **_kwargs: self.fail("unexpected JSON output"),
            redact=lambda value, _secret: value.replace("sk-test", "[redacted]"),
            render=lambda *args, **kwargs: rendered.append((args, kwargs)),
        )

        self.assertEqual(writes, ["[redacted] hi", ""])
        self.assertEqual(rendered[0][0][0], "sk-test hi")
        self.assertEqual(rendered[0][0][6], "served/model")
        self.assertTrue(rendered[0][1]["already_streamed"])
