import importlib.util
import json
import re
import unittest
from functools import lru_cache
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_mcp():
    spec = importlib.util.spec_from_file_location(
        "ambient_mcp_isolation",
        ROOT / "mcp" / "ambient_mcp.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load Ambient MCP module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_schema_properties(schema):
    properties = set()
    if not isinstance(schema, dict):
        return properties
    nested = schema.get("properties", {})
    if isinstance(nested, dict):
        properties.update(nested)
        for child in nested.values():
            properties.update(collect_schema_properties(child))
    items = schema.get("items")
    if isinstance(items, dict):
        properties.update(collect_schema_properties(items))
    return properties


class TestCodexNativeIsolation(unittest.TestCase):
    def test_plugin_bundles_no_default_lifecycle_hooks(self):
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"hooks": {}})

    def test_hook_has_no_claude_plugin_root_fallback(self):
        text = (ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", text)

    def test_skill_forbids_path_first_ambient_routing(self):
        text = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("must never\nrun a bare `ambient` from PATH", text)
        forbidden = [
            "Prefer `ambient`",
            "on PATH when available",
            "Claude-style slash command",
            "AskUserQuestion",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, text)
        self.assertIn("ambient_control", text)
        self.assertIn("ambient_set_mode", text)
        self.assertIn("fresh Codex session", text)

    def test_skill_defaults_to_text_menus_not_native_pickers(self):
        text = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Text menus are the default", text)
        self.assertIn("Do not call `ambient_pick_model` or `ambient_pick_mode`", text)
        self.assertIn("only when the user explicitly asks for a native picker", text)
        self.assertIn("change chat/review model", text)
        self.assertIn("change code/build model", text)
        self.assertIn("change settings", text)
        self.assertIn("browse available models", text)
        self.assertIn("on-demand models are available but may take", text)
        self.assertIn("Audit is a workflow, not", text)
        self.assertIn("Do not repeat those workflow phrases", text)

    def test_codex_facing_docs_do_not_reintroduce_path_first_routing(self):
        docs = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "docs" / "RELEASING.md",
            ROOT / "docs" / "CODEX_NATIVE_ARCHITECTURE.md",
        ]
        pattern = re.compile(r"Prefer `ambient`|on PATH when available|CLAUDE_PLUGIN_ROOT")
        for path in docs:
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))

    def test_security_docs_disclose_optional_local_write_boundaries(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")

        self.assertNotIn(
            "never reads or writes anything outside",
            security,
        )
        for text in (security, privacy):
            self.assertIn("~/.config/opencode/opencode.json", text)
            self.assertIn("`--pure`", text)
        compact_security = re.sub(r"\s+", " ", security)
        self.assertIn("preserves unrelated providers", compact_security)

    def test_docs_do_not_make_native_picker_or_zero_codex_tokens_the_default(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        compact = re.sub(r"\s+", " ", readme)
        forbidden = [
            "Ask Codex to switch models and it calls the MCP tool `ambient_pick_model`",
            "Ambient does everything on your tokens",
            "instead of Codex",
            "zero Codex tokens",
        ]
        for needle in forbidden:
            self.assertNotIn(needle, readme)
        self.assertIn("deterministic text menu first", compact)
        self.assertIn("not the default path", compact)

    def test_architecture_contract_is_codex_native_hybrid(self):
        text = (ROOT / "docs" / "CODEX_NATIVE_ARCHITECTURE.md").read_text(encoding="utf-8")
        expected_tools = {
            "ambient_self_test",
            "ambient_status",
            "ambient_control",
            "ambient_set_mode",
            "ambient_set_model",
            "ambient_pick_model",
            "ambient_pick_mode",
            "ambient_set_config",
            "ambient_key",
            "ambient_models",
            "ambient_doctor",
            "ambient_usage",
            "ambient_ask",
            "ambient_audit_small",
        }
        required = [
            "Ambient Codex is a standalone Codex plugin",
            "intentionally hybrid",
            "Codex skill",
            "MCP server",
            "Bundled CLI",
            "Hooks",
            "Why Not MCP-Only",
            "Why Not CLI-Only",
            "must not depend on a Claude",
            "Hooks are not part of the default runtime path",
        ]
        for needle in required:
            self.assertIn(needle, text)
        self.assertIn("Content-Length framed JSON-RPC", text)
        self.assertIn("newline-delimited JSON-RPC", text)
        tool_section = text.split("## MCP Control Plane", 1)[1].split("## CLI Execution Plane", 1)[0]
        self.assertEqual(set(re.findall(r"- `(ambient_[^`]+)`", tool_section)), expected_tools)

        mcp = load_mcp()
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"].split("+", 1)[0], mcp.SERVER_VERSION)
        long_description = manifest["interface"]["longDescription"]
        self.assertIn("Hooks are not registered by default.", long_description)
        hook_capabilities = [
            item for item in manifest["interface"].get("capabilities", [])
            if "hook" in item.lower()
        ]
        self.assertEqual(hook_capabilities, [])
        hook_advertising = re.compile(r"adds[^.]*hooks|lifecycle hooks|hook support", re.IGNORECASE)
        self.assertRegex("Ambient Codex adds lifecycle hooks.", hook_advertising)
        self.assertIsNone(hook_advertising.search(long_description))

        self.assertEqual({tool["name"] for tool in mcp.TOOLS}, expected_tools)
        self.assertEqual(set(mcp.TOOL_HANDLERS), expected_tools)

        forbidden_secret_args = {
            "ambient_api_key",
            "api_key",
            "apikey",
            "authorization",
            "credential",
            "credentials",
            "key",
            "password",
            "secret",
            "token",
        }
        for tool in mcp.TOOLS:
            with self.subTest(tool=tool["name"]):
                properties = collect_schema_properties(tool["inputSchema"])
                self.assertFalse(properties & forbidden_secret_args)

        with mock.patch.object(mcp.subprocess, "run") as run:
            for secret_name in ("ambient_api_key", "api_key", "key", "token"):
                with self.subTest(secret_name=secret_name):
                    with self.assertRaises(mcp.ToolInputError):
                        mcp.call_tool("ambient_set_config", {
                            "name": secret_name,
                            "value": "amb_should_not_be_accepted",
                        })
        run.assert_not_called()

    def test_mcp_uses_bundled_cli_not_path_lookup(self):
        module = load_mcp()
        self.assertEqual(module.ambient_bin().name, "ambient")
        self.assertIn("ambient-codex", module.ambient_bin().as_posix())

    def test_public_docs_are_portable_and_never_delete_foreign_state(self):
        docs = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "SECURITY.md",
            ROOT / "PRIVACY.md",
            ROOT / "docs" / "RELEASING.md",
            ROOT / "docs" / "STRESS_TEST_PLAN.md",
            ROOT / "docs" / "PRODUCTION_REBUILD_PLAN.md",
        ]
        for path in docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(re.search(r"/Users/[^/\s]+", text))
                self.assertNotIn("rm -rf ~/.config/ambient\n", text)

    def test_public_security_privacy_commands_use_native_launcher(self):
        docs = [ROOT / "SECURITY.md", ROOT / "PRIVACY.md",
                ROOT / "docs" / "RELEASING.md"]
        bare_command = re.compile(
            r"(?m)^\s*ambient (?:setup|cache|ask|audit|map|code|build|agent|"
            r"control|mode|use|doctor|usage|version)\b"
        )
        for path in docs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(bare_command.search(text))
                self.assertNotIn("`ambient agent`", text)
                self.assertNotIn("`ambient --version`", text)

    def test_public_docs_match_the_current_key_isolation_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "CODEX_NATIVE_ARCHITECTURE.md").read_text(
            encoding="utf-8")
        self.assertNotIn("AMBIENT_API_KEY` still works", readme)
        self.assertNotIn("when the key came from the shared variable", security)
        self.assertNotIn("explicitly opt-in, read-only key import", architecture)
        self.assertIn("AMBIENT_API_KEY", readme)
        self.assertIn("ignored", readme.lower())

    def test_published_manifest_links_the_privacy_policy(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        url = manifest["interface"].get("privacyPolicyURL")
        self.assertEqual(
            url,
            "https://github.com/AmbientCrypto/ambient-codex/blob/main/"
            "plugins/ambient-codex/PRIVACY.md",
        )

    def test_skill_has_a_massive_repository_coverage_protocol(self):
        text = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("## Massive Repository Protocol", text)
        self.assertIn("coverage manifest", text)
        self.assertIn("shard", text.lower())
        self.assertIn("exactly once", text)
        self.assertIn("must not claim whole-repository coverage", text)

    def test_ambient_session_docs_explain_fresh_session_reset(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("fresh Codex session starts in Normal\nCodex mode", skill)
        self.assertIn("Ambient session", skill)
        self.assertIn("new Codex thread", readme)
        self.assertIn("`$ambient`", readme)
        self.assertIn("## Codex Session Modes", readme)
        self.assertIn("fresh Codex session begins in\nNormal Codex mode", readme)
        self.assertNotIn(
            "mode setting persists on disk", skill)

    def test_ambient_session_short_asks_are_quiet_and_verbatim(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("routing/checking preamble", skill)
        self.assertIn("Ambient's answer\nverbatim", skill)
        self.assertIn("long-running work", skill)

    def test_ambient_session_long_jobs_use_quiet_liveness_polling(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("one concise running update", skill)
        self.assertIn("material state change", skill)
        self.assertIn("Never cancel a healthy Ambient job", skill)
        self.assertIn("full brief", skill)
        self.assertIn("adaptive recovery signal", skill)

    def test_ambient_session_long_jobs_do_not_create_chatty_poll_loops(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("blocking terminal continuation", skill)
        self.assertIn("maximum host-safe wait interval", skill)
        self.assertIn("not a job timeout", skill)
        self.assertIn("must not emit a chat\n   update", skill)
        self.assertIn("Never cancel from elapsed wall-clock time", skill)
        self.assertIn("material state change", skill)
        self.assertIn("Do not repeat the launch command", skill)

    def test_codex_heavy_jobs_silence_display_only_progress(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("add `--no-progress`", skill)
        self.assertIn("does not weaken", skill)

    def test_takeover_keeps_codex_tools_outside_ambient_boundary(self):
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("Ambient cannot call Codex plugins", skill)
        self.assertIn("Codex is the control plane", skill)
        self.assertIn("Ambient is the generation plane", skill)
        self.assertIn("Do not proxy Codex's tool surface", skill)


if __name__ == "__main__":
    unittest.main()
