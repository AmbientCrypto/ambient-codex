"""Native Codex control surface.

Covers the Codex-owned settings panel, safe mode/model/settings writes, key
offboarding, and MCP write-tool wiring. Hermetic: no live API, temp config only.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "ambient")


def load_module():
    loader = importlib.machinery.SourceFileLoader("ambient_cli_v29", BIN)
    spec = importlib.util.spec_from_loader("ambient_cli_v29", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


amb = load_module()


@contextlib.contextmanager
def patched(obj, **attrs):
    missing = object()
    old = {k: getattr(obj, k, missing) for k in attrs}
    for key, value in attrs.items():
        setattr(obj, key, value)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is missing:
                delattr(obj, key)
            else:
                setattr(obj, key, value)


class NotATTY(io.StringIO):
    def isatty(self):
        return False


CATALOG = [
    {
        "id": "moonshotai/kimi-k2.7-code",
        "name": "Kimi K2.7 Code",
        "is_ready": True,
        "context_length": 262144,
        "max_output_length": 65536,
        "supported_features": ["reasoning", "json_mode"],
    },
    {
        "id": "z-ai/glm-5.2",
        "name": "GLM 5.2",
        "is_ready": False,
        "context_length": 202752,
        "max_output_length": 65536,
        "supported_features": ["reasoning", "json_mode"],
    },
]


class ControlCase(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for key in list(os.environ):
            if key.startswith("AMBIENT_"):
                os.environ.pop(key, None)
        self._td = tempfile.mkdtemp()
        self._cfg = os.path.join(self._td, "env")
        self._orig_cfg = amb.CONFIG_PATH
        amb.CONFIG_PATH = self._cfg

    def tearDown(self):
        amb.CONFIG_PATH = self._orig_cfg
        shutil.rmtree(self._td, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env)

    def parse(self, *argv):
        return amb.build_parser().parse_args(["control", *argv])

    def run_control(self, *argv):
        args = self.parse(*argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            amb.cmd_control(args)
        return out.getvalue(), err.getvalue()


class ControlSnapshotTests(ControlCase):
    def test_json_snapshot_is_native_redacted_and_actionable(self):
        amb.save_config_values({
            "AMBIENT_DELEGATE": "on",
            "AMBIENT_MODEL": "z-ai/glm-5.2",
            "AMBIENT_CODE_MODEL": "moonshotai/kimi-k2.7-code",
        })
        secret = "amb_abcdefghijklmnopqrstuvwxyz"
        with patched(amb, resolve_key_and_backend=lambda conf: (secret, "keychain"),
                     safe_catalog=lambda api_url, api_key: CATALOG):
            out, _ = self.run_control("--json")
        self.assertNotIn(secret, out)
        data = json.loads(out)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["surface"], "codex-native")
        self.assertEqual(data["mode"], "on")
        self.assertEqual(data["key"], {"configured": True, "backend": "keychain"})
        self.assertEqual(data["defaults"]["chat"], "z-ai/glm-5.2")
        self.assertEqual(data["models"]["serving_count"], 1)
        self.assertIn(
            {"state": "takeover",
             "label": "Ambient session",
             "description": "Ambient is the direct chat and work engine for this session.",
             "current": False},
            data["mode_options"],
        )
        self.assertIn(
            {"phrase": "change settings",
             "description": "edit streaming, fallback, and optional savings"},
            data["chat_actions"],
        )
        self.assertIn(
            {"phrase": "set one model for all work",
             "description": "set the same default for chat/review and code/build work"},
            data["chat_actions"],
        )
        self.assertIn(
            {"phrase": "browse available models",
             "description": "view serving and on-demand models without changing a default"},
            data["chat_actions"],
        )
        self.assertIn(
            {"phrase": "change chat/review model",
             "description": "override the model for asks and audits only"},
            data["chat_actions"],
        )
        self.assertIn(
            {"phrase": "change code/build model",
             "description": "override the model for code, builds, and agent work only"},
            data["chat_actions"],
        )
        self.assertIn(
            {"phrase": "audit this diff",
             "description": "second-opinion review of the current git diff"},
            data["workflows"],
        )
        control_phrases = {action["phrase"] for action in data["chat_actions"]}
        workflow_phrases = {workflow["phrase"] for workflow in data["workflows"]}
        self.assertFalse(control_phrases & workflow_phrases)
        self.assertIn("ambient-codex control mode on", data["actions"])
        self.assertIn("ambient-codex setup", data["actions"])
        setting_names = [setting["name"] for setting in data["settings"]]
        self.assertNotIn("reference-price", setting_names)
        self.assertNotIn("reference-price", "\n".join(data["actions"]))
        self.assertEqual(setting_names, ["streaming", "fallback", "savings"])
        self.assertNotIn("spend-cap", "\n".join(data["actions"]))

    def test_text_panel_exposes_controls_without_network_requirement(self):
        amb.save_config_values({"AMBIENT_DELEGATE": "off"})
        with patched(amb, safe_catalog=lambda api_url, api_key: []):
            out, _ = self.run_control("--offline")
        self.assertIn("Ambient Codex Control", out)
        self.assertIn("API key", out)
        self.assertIn("Mode", out)
        self.assertIn("Modes:", out)
        self.assertIn("Delegate", out)
        self.assertIn("direct chat and work engine", out)
        self.assertIn("Workflows:", out)
        self.assertIn("ambient-codex control model MODEL --chat", out)
        self.assertNotIn("spend-cap", out)
        self.assertIn("In Codex chat, say:", out)
        self.assertIn(
            "set one model for all work  set the same default for chat/review and code/build work",
            out,
        )
        for phrase, _description in amb.CONTROL_CHAT_ACTIONS:
            self.assertIn(phrase, out)
        workflow_phrases = [phrase for phrase, _description in amb.CONTROL_WORKFLOWS]
        for phrase in workflow_phrases:
            self.assertEqual(out.count(phrase), 1, phrase)
        self.assertIn("(workflow commands are listed above)", out)


class ControlWriteTests(ControlCase):
    def test_mode_action_updates_delegate_state(self):
        out, _ = self.run_control("mode", "takeover")
        self.assertIn("Ambient Takeover: ON", out)
        self.assertEqual(amb.read_config_file().get("AMBIENT_DELEGATE"), "takeover")

    def test_model_action_can_update_only_code_lane_without_key(self):
        with patched(amb, fetch_models=lambda api_url, api_key: CATALOG):
            out, _ = self.run_control("model", "glm", "--code")
        conf = amb.read_config_file()
        self.assertIn("z-ai/glm-5.2", out)
        self.assertNotIn("AMBIENT_MODEL", conf)
        self.assertEqual(conf.get("AMBIENT_CODE_MODEL"), "z-ai/glm-5.2")

    def test_setting_action_uses_config_whitelist(self):
        out, _ = self.run_control("setting", "fallback", "on")
        self.assertIn("fallback = on", out)
        self.assertEqual(amb.read_config_file().get("AMBIENT_FALLBACK"), "on")
        out, _ = self.run_control("setting", "fallback", "--unset")
        self.assertIn("back to default", out)
        self.assertNotIn("AMBIENT_FALLBACK", amb.read_config_file())

    def test_key_setup_in_non_tty_gives_instructions_not_prompt(self):
        with patched(amb.sys, stdin=NotATTY(), stdout=io.StringIO(), stderr=io.StringIO()):
            out, _ = self.run_control("key", "setup")
        self.assertIn("ambient-codex setup", out)
        self.assertIn("app.ambient.xyz", out)   # points the user at the key console

    def test_key_remove_scrubs_file_and_secret_store(self):
        amb.save_config_values({
            "AMBIENT_API_KEY": "amb_abcdefghijklmnopqrstuvwxyz",
            "AMBIENT_KEY_BACKEND": "file",
        })
        with patched(amb, keychain_delete=lambda: True, keychain_read=lambda: None):
            out, _ = self.run_control("key", "remove")
        self.assertIn("Key removed", out)
        conf = amb.read_config_file()
        self.assertNotIn("AMBIENT_API_KEY", conf)
        self.assertNotIn("AMBIENT_KEY_BACKEND", conf)


class RegistryDocsContractTests(unittest.TestCase):
    def test_control_is_registered_keyless(self):
        spec = next(s for s in amb.COMMANDS if s["name"] == "control")
        self.assertIs(spec["needs_key"], False)
        self.assertEqual(spec["handler"], "cmd_control")

    def test_parser_accepts_native_control_forms(self):
        parser = amb.build_parser()
        self.assertEqual(parser.parse_args(["control"]).command, "control")
        self.assertEqual(parser.parse_args(["control", "--json"]).json, True)
        model = parser.parse_args(["control", "model", "m", "--chat"])
        self.assertEqual((model.control_action, model.model_id, model.chat),
                         ("model", "m", True))
        setting = parser.parse_args(["control", "setting", "fallback", "--unset"])
        self.assertEqual((setting.control_action, setting.name, setting.unset),
                         ("setting", "fallback", True))


if __name__ == "__main__":
    unittest.main()


class KeyOnboardingUXTests(unittest.TestCase):
    """The no-key path must clearly say: get a key at app.ambient.xyz, run `ambient-codex setup`."""

    def _no_key(self):
        return patched(
            amb,
            resolve_key_and_backend=lambda conf: (None, None),
            read_config_file=lambda: {},
        )

    def test_key_status_names_the_console_and_the_clean_command(self):
        with self._no_key(), patched(amb.sys, stdout=io.StringIO(),
                                     stderr=io.StringIO()) as _:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                amb._control_print_key_status()
            out = buf.getvalue()
        self.assertIn("app.ambient.xyz", out)
        self.assertIn("ambient-codex setup", out)
        self.assertNotIn("control key setup", out)

    def test_setup_instruction_leads_with_the_console_and_setup(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            amb._control_setup_instruction("setup")
        out = buf.getvalue()
        self.assertIn("ambient-codex setup", out)
        self.assertIn("app.ambient.xyz", out)
        self.assertNotIn("control key setup", out)

    def test_actions_offer_setup_not_control_key_setup(self):
        self.assertIn("ambient-codex setup", amb.CONTROL_ACTIONS)
        self.assertNotIn("ambient-codex control key setup", amb.CONTROL_ACTIONS)
        for action in (
            "ask PROMPT",
            "audit --staged --json",
            "build TASK --dir DIR --json",
            "doctor",
            "usage --json",
        ):
            self.assertIn(f"ambient-codex {action}", amb.CONTROL_ACTIONS)
