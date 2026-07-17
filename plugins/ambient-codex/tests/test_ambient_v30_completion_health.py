"""Final-answer health, no-rebill, and explicit-model regression tests."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import os
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "ambient")


def load_module():
    loader = importlib.machinery.SourceFileLoader("ambient_cli_v30_health", BIN)
    spec = importlib.util.spec_from_loader("ambient_cli_v30_health", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


amb = load_module()


def catalog():
    return [
        {
            "id": "cheap/unreliable",
            "context_length": 200_000,
            "max_output_length": 65_536,
            "is_ready": True,
            "supported_features": ["reasoning", "structured_outputs"],
            "output_modalities": ["text"],
            "pricing": {"input": 0.1, "output": 0.2},
        },
        {
            "id": "healthy/model",
            "context_length": 200_000,
            "max_output_length": 65_536,
            "is_ready": True,
            "supported_features": ["reasoning", "structured_outputs"],
            "output_modalities": ["text"],
            "pricing": {"input": 0.2, "output": 0.4},
        },
    ]


def ns(**updates):
    values = {
        "max_tokens": 8_000,
        "temperature": 0.1,
        "timeout": 30,
        "raw": False,
        "fallback": False,
        "allow_partial": False,
        "allow_cost": True,
        "yes": True,
        "no_cache": True,
        "cache_ttl": None,
        "model": None,
        "escalation_ceiling": 30_000,
        "_auto_budget": True,
        "max_budget_escalations": 0,
        "accept_reasoning_draft": False,
    }
    return argparse.Namespace(**{**values, **updates})


def ok_body(content="ok", reasoning=""):
    return 200, {
        "content": content,
        "reasoning": reasoning,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "finish_reason": "stop",
    }


def stream_sequence(*responses):
    calls = []
    queue = list(responses)

    def fake(_url, _key, payload, _timeout, on_delta=None):
        calls.append(payload)
        if not queue:
            raise AssertionError("unexpected extra completion call")
        return queue.pop(0)

    return fake, calls


@contextlib.contextmanager
def patched(obj, **attributes):
    original = {name: getattr(obj, name) for name in attributes}
    for name, value in attributes.items():
        setattr(obj, name, value)
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(obj, name, value)


class CompletionHealthTests(unittest.TestCase):
    def setUp(self):
        self.original_log_usage = amb.log_usage
        amb.log_usage = lambda *args, **kwargs: None

    def tearDown(self):
        amb.log_usage = self.original_log_usage

    def test_code_never_accepts_internal_reasoning_as_deliverable(self):
        parsed = amb.build_parser().parse_args(["code", "repair", "one", "file"])
        self.assertFalse(parsed.accept_reasoning_draft)
        self.assertEqual(parsed.max_budget_escalations, 0)

    def test_reasoning_only_code_fails_once_and_records_health_failure(self):
        stream, calls = stream_sequence(ok_body(content="", reasoning="R" * 500))
        outcomes = []

        with patched(
            amb,
            stream_completion=stream,
            record_cap=lambda model, dimension, ok: outcomes.append(
                (model, dimension, ok)
            ),
        ):
            with self.assertRaises(amb.ChatError) as raised:
                amb.complete(
                    "key", "url", "reasoning/model",
                    [{"role": "user", "content": "small"}], ns()
                )

        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.category, "empty")
        self.assertIn("retry disabled", raised.exception.diagnosis)
        self.assertEqual(outcomes, [("reasoning/model", "final_answer", False)])

    def test_successful_completion_records_final_answer_health(self):
        stream, _calls = stream_sequence(ok_body(content="complete artifact"))
        outcomes = []

        with patched(
            amb,
            stream_completion=stream,
            record_cap=lambda model, dimension, ok: outcomes.append(
                (model, dimension, ok)
            ),
        ):
            content, _usage, _body = amb.complete(
                "key", "url", "healthy/model",
                [{"role": "user", "content": "small"}], ns()
            )

        self.assertEqual(content, "complete artifact")
        self.assertEqual(outcomes, [("healthy/model", "final_answer", True)])

    def test_auto_routing_avoids_learned_final_answer_failure(self):
        args = argparse.Namespace(command="code", model="auto", json=False)

        with patched(
            amb,
            cap_state=lambda model, dimension: (
                "unreliable"
                if model == "cheap/unreliable" and dimension == "final_answer"
                else "unknown"
            ),
        ):
            selected = amb.resolve_auto_model(
                "auto", catalog(), {}, input_chars=100, args=args
            )

        self.assertEqual(selected, "healthy/model")

    def test_explicit_model_remains_sacred_even_when_health_is_unreliable(self):
        args = argparse.Namespace(command="code", model="cheap/unreliable", json=False)

        with patched(amb, cap_state=lambda _model, _dimension: "unreliable"):
            selected = amb.route_model(args, {}, "code", catalog(), input_chars=100)

        self.assertEqual(selected, "cheap/unreliable")


if __name__ == "__main__":
    unittest.main()
