"""Phase 5 contract for the money-safe public usage projection."""

import importlib
from types import SimpleNamespace
import unittest


class OutputSchemaTests(unittest.TestCase):
    def test_agent_provider_merge_is_immutable_and_namespaced(self):
        core = importlib.import_module("ambient_codex.agent_config")
        source = {"provider": {
            "foreign": {"options": {"apiKey": "foreign"}},
            "ambient-codex": {"models": {"old/model": {"name": "old/model"}}},
        }}
        updated = core.update_provider_config(
            source, provider="ambient-codex", api_url="https://api.example", model="new/model")
        self.assertEqual(source["provider"]["ambient-codex"],
                         {"models": {"old/model": {"name": "old/model"}}})
        self.assertEqual(updated["provider"]["foreign"], source["provider"]["foreign"])
        ours = updated["provider"]["ambient-codex"]
        self.assertEqual(ours["options"]["baseURL"], "https://api.example/v1")
        self.assertEqual(set(ours["models"]), {"old/model", "new/model"})
        self.assertIsNone(core.update_provider_config(
            {"provider": []}, provider="ambient-codex", api_url="https://api.example", model="new/model"))

    def test_agent_command_is_namespaced_pure_and_never_accepts_a_key(self):
        core = importlib.import_module("ambient_codex.agent_config")
        self.assertEqual(
            core.build_agent_argv(
                ["run", "ship", "it"], provider="ambient-codex",
                model="new/model"),
            ["opencode", "run", "--model", "ambient-codex/new/model",
             "--pure", "ship", "it"],
        )
        self.assertEqual(
            core.build_agent_argv(
                ["--no-pure", "chat"], provider="ambient-codex",
                model="new/model"),
            ["opencode", "--model", "ambient-codex/new/model",
             "--no-pure", "chat"],
        )
        self.assertNotIn("api_key", core.build_agent_argv.__code__.co_varnames)

    def test_launcher_ownership_checks_use_injected_filesystem_reads(self):
        core = importlib.import_module("ambient_codex.launcher")
        self.assertTrue(core.owned_link(
            "/tmp/ambient-codex", is_link=lambda _path: True,
            read_link=lambda _path: "/cache/ambient-codex/1/bin/ambient"))
        self.assertFalse(core.owned_link(
            "/tmp/ambient-codex", is_link=lambda _path: True,
            read_link=lambda _path: "/other/ambient"))
        self.assertFalse(core.owned_link(
            "/tmp/ambient-codex", is_link=lambda _path: False,
            read_link=lambda _path: self.fail("must not read a regular file")))
        self.assertTrue(core.owned_shim(
            "/tmp/ambient-codex.cmd",
            read_text=lambda _path: '@python "C:\\\\cache\\\\ambient-codex\\\\bin\\\\ambient" %*'))
        self.assertFalse(core.owned_shim(
            "/tmp/foreign.cmd", read_text=lambda _path: '@python "C:\\\\other\\\\tool" %*'))
        self.assertTrue(core.owned_file(
            "/tmp/ambient-codex", read_text=lambda _path:
            '# ambient-codex stable launcher v1'))
        self.assertEqual(core.__all__, (
            "owned_link", "owned_shim", "owned_file"))

    def test_module_allowlists_tokens_and_never_mutates_input(self):
        core = importlib.import_module("ambient_codex.output_schema")
        source = {"prompt_tokens": 1, "completion_tokens": 2,
                  "cost": 0.01, "price": 1, "saved_pct": 99}
        self.assertEqual(core.__all__, (
            "public_usage", "build_envelope", "build_error_envelope",
            "render_terminal_result",
        ))
        self.assertEqual(core.public_usage(source),
                         {"prompt_tokens": 1, "completion_tokens": 2})
        self.assertIn("cost", source)

    def test_envelope_marks_token_cap_as_partial_without_money_fields(self):
        core = importlib.import_module("ambient_codex.output_schema")
        envelope, code = core.build_envelope(
            "audit", model="model", usage={"prompt_tokens": 1, "cost": 2},
            finish_reason="length", allow_partial=False, partial_exit_code=2,
        )
        self.assertEqual(code, 2)
        self.assertEqual(envelope["status"], "partial")
        self.assertNotIn("cost", envelope["usage"])

    def test_error_envelope_uses_the_same_versioned_public_schema(self):
        core = importlib.import_module("ambient_codex.output_schema")
        self.assertEqual(
            core.build_error_envelope("map", "usage", "bad input", 64),
            {"schema_version": 1, "kind": "map", "status": "error",
             "category": "usage", "diagnosis": "bad input", "exit_code": 64},
        )

    def test_terminal_renderer_keeps_savings_relative_and_honors_partial_exit(self):
        core = importlib.import_module("ambient_codex.output_schema")
        stdout, stderr, exits = [], [], []
        core.render_terminal_result(
            "done", False, "", SimpleNamespace(allow_partial=False), "secret",
            usage={"prompt_tokens": 1, "completion_tokens": 2}, model="model",
            already_streamed=False, usage_by_model=None,
            paint=lambda text, _style: text,
            redact=lambda text, _secret: text.replace("secret", "[redacted]"),
            stdout=SimpleNamespace(write=stdout.append, flush=lambda: None),
            emit_stdout=stdout.append, emit_stderr=stderr.append,
            exit_process=exits.append, partial_exit_code=2,
            savings_note=lambda _model, _usage: " ~60% cheaper",
            savings_note_by_served=lambda _usage: "",
            reasoning_hint=lambda _text, _tokens: "",
        )
        self.assertEqual(stdout, ["done"])
        self.assertEqual(stderr, ["\n[ambient model | in=1 out=2 tokens ~60% cheaper]"])
        self.assertEqual(exits, [])
