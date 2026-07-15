import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MCP = ROOT / "mcp" / "ambient_mcp.py"


def load_mcp():
    spec = importlib.util.spec_from_file_location("ambient_mcp", MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plugin_version():
    manifest = ROOT / ".codex-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"].split("+", 1)[0]


def encode_frame(payload):
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def encode_jsonl(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def decode_frames(raw):
    frames = []
    rest = raw
    while rest:
        head, sep, tail = rest.partition(b"\r\n\r\n")
        if not sep:
            break
        length = None
        for line in head.decode("ascii").splitlines():
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        if length is None:
            break
        body = tail[:length]
        frames.append(json.loads(body.decode("utf-8")))
        rest = tail[length:]
    return frames


def decode_jsonl(raw):
    return [json.loads(line.decode("utf-8")) for line in raw.splitlines() if line.strip()]


class TestMcpAdapter(unittest.TestCase):
    def test_mcp_version_matches_plugin_manifest(self):
        mcp = load_mcp()
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp.SERVER_VERSION, manifest["version"].split("+", 1)[0])

    def test_plugin_root_ignores_stale_plugin_root_env(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "missing-cache-version"
            with mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(stale)}):
                self.assertEqual(mcp.plugin_root(), ROOT.resolve())
                self.assertEqual(mcp.ambient_bin(), ROOT.resolve() / "bin" / "ambient")

    def test_plugin_root_honors_valid_plugin_root_env(self):
        mcp = load_mcp()
        with mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(ROOT)}):
            self.assertEqual(mcp.plugin_root(), ROOT.resolve())

    def test_plugin_root_falls_back_to_current_sibling_cache(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            cache_parent = Path(tmp) / "cache" / "ambient-codex" / "ambient-codex"
            stale = cache_parent / "1.8.5"
            current = cache_parent / mcp.SERVER_VERSION
            (stale / "mcp").mkdir(parents=True)
            (current / "bin").mkdir(parents=True)
            (current / ".codex-plugin").mkdir()
            (current / "bin" / "ambient").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (current / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": mcp.SERVER_NAME, "version": mcp.SERVER_VERSION}),
                encoding="utf-8",
            )
            fake_file = stale / "mcp" / "ambient_mcp.py"
            fake_file.write_text("# stale module path\n", encoding="utf-8")
            with mock.patch.object(mcp, "__file__", str(fake_file)), \
                 mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(stale)}):
                self.assertEqual(mcp.plugin_root(), current.resolve())

    def test_stdio_initialize_and_list_tools(self):
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        proc = subprocess.run(
            [sys.executable, str(MCP)],
            input=encode_frame(init) + encode_frame(tools),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        frames = decode_frames(proc.stdout)
        self.assertEqual(frames[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(frames[0]["result"]["serverInfo"]["name"], "ambient-codex")
        self.assertEqual(frames[0]["result"]["capabilities"]["tools"], {})
        self.assertIn("instructions", frames[0]["result"])
        self.assertIn("bundled Ambient CLI", frames[0]["result"]["instructions"])
        names = {tool["name"] for tool in frames[1]["result"]["tools"]}
        self.assertIn("ambient_status", names)
        self.assertIn("ambient_ask", names)
        self.assertIn("ambient_control", names)
        self.assertIn("ambient_set_mode", names)
        self.assertIn("ambient_set_model", names)
        self.assertIn("ambient_set_config", names)
        self.assertIn("ambient_key", names)
        self.assertIn("ambient_self_test", names)

    def test_stdio_jsonl_initialize_and_list_tools(self):
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "codex-jsonl", "version": "0"},
            },
        }
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        proc = subprocess.run(
            [sys.executable, str(MCP)],
            input=encode_jsonl(init) + encode_jsonl({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }) + encode_jsonl(tools),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        frames = decode_jsonl(proc.stdout)
        self.assertEqual(frames[0]["result"]["serverInfo"]["version"], plugin_version())
        self.assertEqual(len(frames[1]["result"]["tools"]), len(load_mcp().TOOLS))

    def test_content_length_rejects_invalid_or_oversized_frames(self):
        mcp = load_mcp()
        cases = [
            ("0", b""),
            ("-1", b""),
            ("not-a-number", b""),
            (str(mcp.MAX_FRAME_BYTES + 1), b""),
        ]
        for length, body in cases:
            with self.subTest(length=length):
                frame = (f"Content-Length: {length}\r\n\r\n".encode("ascii") + body)
                with self.assertRaises(ValueError):
                    mcp.read_message(io.BytesIO(frame))

    def test_content_length_rejects_truncated_body(self):
        mcp = load_mcp()
        frame = b"Content-Length: 3\r\n\r\n{}"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            mcp.read_message(io.BytesIO(frame))

    def test_notifications_and_ping_are_codex_safe(self):
        mcp = load_mcp()
        self.assertIsNone(mcp.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }))
        self.assertIsNone(mcp.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 9, "reason": "client cancelled"},
        }))
        response = mcp.handle_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_empty_resource_and_prompt_lists_are_supported(self):
        mcp = load_mcp()
        resources = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        templates = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"})
        prompts = mcp.handle_request({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})
        self.assertEqual(resources["result"], {"resources": []})
        self.assertEqual(templates["result"], {"resourceTemplates": []})
        self.assertEqual(prompts["result"], {"prompts": []})

    def test_batch_requests_return_only_call_responses(self):
        mcp = load_mcp()
        response = mcp.handle_payload([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        self.assertEqual([item["id"] for item in response], [1, 2])
        self.assertIn("tools", response[1]["result"])

    def test_plugin_mcp_config_is_python_only_unbuffered_and_bounded(self):
        data = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        ambient = data["mcpServers"]["ambient"]
        self.assertEqual(ambient["command"], "python3")
        self.assertEqual(ambient["args"], ["-u", "mcp/ambient_mcp.py"])
        self.assertEqual(ambient["cwd"], ".")
        self.assertGreaterEqual(ambient["startup_timeout_sec"], 60)
        self.assertEqual(ambient["tool_timeout_sec"], 120)

    def test_no_node_artifact_survives_anywhere_in_the_plugin(self):
        """Node must never re-enter the MCP critical path.

        The 1.5.x plugin shipped a Node launcher whose only job was to locate
        python3. Codex installed from Homebrew/standalone has no Node, so the
        MCP server never started. Guard the whole tree, not just .mcp.json.
        """
        self.assertFalse((ROOT / "mcp" / "ambient_mcp_launcher.js").exists())
        self.assertEqual(sorted(p.name for p in (ROOT / "mcp").glob("*.js")), [])
        blob = json.dumps(json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8")))
        self.assertNotIn("node", blob)

    def test_python3_directly_starts_framed_mcp_server(self):
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        proc = subprocess.run(
            [sys.executable, "-u", str(MCP)],
            cwd=ROOT,
            input=encode_frame(init) + encode_frame({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }) + encode_frame(tools),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        frames = decode_frames(proc.stdout)
        self.assertEqual(frames[0]["result"]["serverInfo"]["version"], plugin_version())
        self.assertEqual(len(frames[1]["result"]["tools"]), len(load_mcp().TOOLS))

    def test_python3_directly_starts_jsonl_mcp_server(self):
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "codex-jsonl", "version": "0"},
            },
        }
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        proc = subprocess.run(
            [sys.executable, "-u", str(MCP)],
            cwd=ROOT,
            input=encode_jsonl(init) + encode_jsonl(tools),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        frames = decode_jsonl(proc.stdout)
        self.assertEqual(frames[0]["result"]["serverInfo"]["version"], plugin_version())
        self.assertEqual(len(frames[1]["result"]["tools"]), len(load_mcp().TOOLS))

    def test_mcp_server_starts_with_node_absent_from_path(self):
        """The regression that shipped: reproduce a node-free Codex install.

        Build a PATH that contains python3 and nothing else, assert node really
        is unreachable from it, then start the server exactly as .mcp.json does.
        """
        if os.name == "nt":  # pragma: no cover - PATH shim semantics differ
            self.skipTest("POSIX PATH shim")
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink(sys.executable, os.path.join(tmp, "python3"))
            self.assertIsNone(shutil.which("node", path=tmp))
            self.assertIsNotNone(shutil.which("python3", path=tmp))
            cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
            ambient = cfg["mcpServers"]["ambient"]

            proc = subprocess.run(
                [ambient["command"], *ambient["args"]],
                cwd=ROOT,
                env={"PATH": tmp, "HOME": tmp},
                input=encode_jsonl({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                }) + encode_jsonl({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
            frames = decode_jsonl(proc.stdout)
            self.assertEqual(frames[0]["result"]["serverInfo"]["version"], plugin_version())
            self.assertEqual(len(frames[1]["result"]["tools"]), len(load_mcp().TOOLS))

    def test_all_mcp_tool_schemas_are_codex_strict_objects(self):
        mcp = load_mcp()
        for tool in mcp.TOOLS:
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertIsInstance(schema["properties"], dict)
                self.assertIsInstance(schema["required"], list)
                self.assertIs(schema["additionalProperties"], False)
                for required_name in schema["required"]:
                    self.assertIn(required_name, schema["properties"])

    def test_self_test_is_local_bounded_and_redacted(self):
        mcp = load_mcp()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ambient 1.5.7\n",
            stderr="",
        )
        with mock.patch.object(mcp.subprocess, "run", return_value=completed) as run:
            result = mcp.call_tool("ambient_self_test", {})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "version")
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        text = result["content"][0]["text"]
        self.assertIn("ambient-codex self-test ok", text)
        self.assertNotIn("AMBIENT_API_KEY", text)

    def test_missing_cli_reports_stale_mcp_server(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing-root"
            with mock.patch.object(mcp, "plugin_root", return_value=root):
                with self.assertRaises(mcp.AmbientCommandError) as caught:
                    mcp.run_ambient(["config"])
        message = str(caught.exception)
        self.assertIn("pre-update MCP server", message)
        self.assertIn("Restart Codex", message)

    def test_self_test_missing_cli_reports_restart_guidance(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugin"
            root.mkdir()
            with mock.patch.object(mcp, "plugin_root", return_value=root):
                result = mcp.call_tool("ambient_self_test", {})
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("missing bundled CLI", text)
        self.assertIn("Restart Codex", text)

    def test_status_tool_runs_config_with_redaction(self):
        mcp = load_mcp()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="AMBIENT_API_KEY=amb_abcdefghijklmnopqrstuvwxyz\nok\n",
            stderr="",
        )
        with mock.patch.object(mcp.subprocess, "run", return_value=completed) as run:
            result = mcp.call_tool("ambient_status", {})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "config")
        text = result["content"][0]["text"]
        self.assertIn("AMBIENT_API_KEY=<redacted>", text)
        self.assertNotIn("amb_abcdefghijklmnopqrstuvwxyz", text)
        self.assertFalse(result.get("isError", False))

    def test_control_tool_runs_bundled_control_json(self):
        mcp = load_mcp()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"schema_version":1,"surface":"codex-native"}\n',
            stderr="",
        )
        with mock.patch.object(mcp.subprocess, "run", return_value=completed) as run:
            result = mcp.call_tool("ambient_control", {"offline": True})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:], ["control", "--json", "--offline"])
        self.assertIn("codex-native", result["content"][0]["text"])

    def test_mode_is_session_scoped_while_other_setters_use_control_subcommands(self):
        mcp = load_mcp()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
        with mock.patch.object(mcp.subprocess, "run", return_value=completed) as run:
            mode = mcp.call_tool("ambient_set_mode", {"state": "takeover"})
            mcp.call_tool("ambient_set_model", {"model": "z-ai/glm-5.2", "lane": "code"})
            mcp.call_tool("ambient_set_config", {"name": "fallback", "value": "on"})
            mcp.call_tool("ambient_set_config", {"name": "fallback", "unset": True})
        self.assertFalse(mode["isError"])
        self.assertEqual(mcp.SESSION.mode, "takeover")
        self.assertIn("This Codex session only", mode["content"][0]["text"])
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0][-5:], ["control", "model", "--code", "--", "z-ai/glm-5.2"])
        self.assertEqual(calls[1][-5:], ["control", "setting", "fallback", "--", "on"])
        self.assertEqual(calls[2][-4:], ["control", "setting", "fallback", "--unset"])

    def test_control_reports_the_live_session_mode_not_persisted_mode(self):
        mcp = load_mcp()
        mcp.SESSION.mode = "takeover"
        payload = {
            "schema_version": 1,
            "mode": "off",
            "mode_options": [
                {"state": state, "current": state == "off"}
                for state in ("off", "on", "takeover")
            ],
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(mcp.subprocess, "run", return_value=completed):
            result = mcp.call_tool("ambient_control", {"offline": True})
        snapshot = json.loads(result["content"][0]["text"])
        self.assertEqual(snapshot["mode"], "takeover")
        self.assertEqual(
            [option["state"] for option in snapshot["mode_options"] if option["current"]],
            ["takeover"],
        )

    def test_ask_and_audit_cli_arguments_cannot_be_reinterpreted_as_flags(self):
        mcp = load_mcp()
        completed = {"content": [], "isError": False}
        with mock.patch.object(mcp, "run_ambient", return_value=completed) as run:
            mcp.call_tool("ambient_ask", {
                "prompt": "--model",
                "system": "--ignore-this-as-a-flag",
                "model": "--not-a-model-flag",
                "json": False,
            })
            ask_argv = run.call_args_list[-1].args[0]
            mcp.call_tool("ambient_audit_small", {
                "paths": ["--focus", "safe.py"],
                "diff": "--cached",
                "focus": "--system",
                "json": False,
            })
            audit_argv = run.call_args_list[-1].args[0]
        self.assertEqual(ask_argv, [
            "ask", "--yes", "--system=--ignore-this-as-a-flag",
            "--model=--not-a-model-flag", "--", "--model",
        ])
        self.assertEqual(audit_argv, [
            "audit", "--diff=--cached", "--focus=--system", "--yes",
            "--", "--focus", "safe.py",
        ])

    def test_ask_tool_compacts_successful_json_to_the_ambient_answer(self):
        mcp = load_mcp()
        payload = {
            "kind": "ask",
            "status": "ok",
            "content": "Ambient's concise answer.",
            "model": "ambient/large",
            "usage": {"prompt_tokens": 12, "completion_tokens": 34},
        }
        completed = mcp.tool_text(json.dumps(payload))
        with mock.patch.object(mcp, "run_ambient", return_value=completed):
            result = mcp.call_tool("ambient_ask", {"prompt": "Hello"})
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], "Ambient's concise answer.")

    def test_ask_tool_marks_compacted_partial_results_incomplete(self):
        mcp = load_mcp()
        payload = {
            "kind": "ask",
            "status": "ok",
            "content": "The partial answer.",
            "partial": True,
            "finish_reason": "length",
        }
        completed = mcp.tool_text(json.dumps(payload))
        with mock.patch.object(mcp, "run_ambient", return_value=completed):
            result = mcp.call_tool("ambient_ask", {"prompt": "Hello"})
        self.assertIn("The partial answer.", result["content"][0]["text"])
        self.assertIn("incomplete", result["content"][0]["text"])

    def test_ask_tool_preserves_error_details_for_diagnosis(self):
        mcp = load_mcp()
        completed = mcp.tool_text("ambient exited 1\nsetup required", is_error=True)
        with mock.patch.object(mcp, "run_ambient", return_value=completed):
            result = mcp.call_tool("ambient_ask", {"prompt": "Hello"})
        self.assertEqual(result, completed)

    def test_mcp_string_inputs_reject_nul_bytes_before_subprocess(self):
        mcp = load_mcp()
        with mock.patch.object(mcp, "run_ambient") as run:
            cases = [
                ("ambient_ask", {"prompt": "safe\x00--model evil"}),
                ("ambient_set_model", {"model": "safe\x00model", "lane": "both"}),
                ("ambient_set_config", {"name": "fallback", "value": "on\x00"}),
                ("ambient_audit_small", {"paths": ["safe\x00.py"]}),
            ]
            for name, arguments in cases:
                with self.subTest(name=name):
                    with self.assertRaises(mcp.ToolInputError):
                        mcp.call_tool(name, arguments)
        run.assert_not_called()

    def test_internal_mcp_errors_are_redacted(self):
        mcp = load_mcp()
        original = mcp.TOOL_HANDLERS["ambient_status"]

        def explode(_args):
            raise RuntimeError("AMBIENT_API_KEY=amb_abcdefghijklmnopqrstuvwxyz")

        mcp.TOOL_HANDLERS["ambient_status"] = explode
        try:
            response = mcp.handle_request({
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "ambient_status", "arguments": {}},
            })
        finally:
            mcp.TOOL_HANDLERS["ambient_status"] = original
        message = response["error"]["message"]
        self.assertNotIn("amb_abcdefghijklmnopqrstuvwxyz", message)
        self.assertIn("<redacted>", message)

    def test_set_config_schema_exposes_only_native_user_settings(self):
        mcp = load_mcp()
        tool = next(tool for tool in mcp.TOOLS if tool["name"] == "ambient_set_config")
        names = tool["inputSchema"]["properties"]["name"]["enum"]
        self.assertEqual(
            names,
            ["streaming", "fallback", "savings"])

    def test_setters_validate_before_subprocess(self):
        mcp = load_mcp()
        with mock.patch.object(mcp.subprocess, "run") as run:
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_set_mode", {"state": "bogus"})
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_set_model", {"model": "x", "lane": "bad"})
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_set_config", {"name": "spend-cap", "value": "12"})
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_set_config", {"name": "reference-price", "value": "3/15"})
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_set_config", {"name": "fallback", "value": "on", "unset": True})
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_key", {"action": "add", "key": "amb_abcdefghijklmnopqrstuvwxyz"})
        run.assert_not_called()

    def test_key_tool_never_accepts_key_material(self):
        mcp = load_mcp()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="key missing\n", stderr="")
        with mock.patch.object(mcp.subprocess, "run", return_value=completed) as run:
            mcp.call_tool("ambient_key", {"action": "status"})
            mcp.call_tool("ambient_key", {"action": "setup"})
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0][-3:], ["control", "key", "status"])
        self.assertEqual(calls[1][-3:], ["control", "key", "setup"])

    def test_ask_tool_rejects_huge_prompt_before_subprocess(self):
        mcp = load_mcp()
        with mock.patch.object(mcp.subprocess, "run") as run:
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_ask", {"prompt": "x" * 60001})
        run.assert_not_called()

    def test_audit_small_rejects_oversized_path_before_subprocess(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.py"
            path.write_bytes(b"x\n" * ((mcp.MAX_AUDIT_PATH_BYTES // 2) + 1))
            with mock.patch.object(mcp, "run_ambient") as run:
                with self.assertRaises(mcp.ToolInputError):
                    mcp.call_tool("ambient_audit_small", {
                        "paths": [str(path)],
                        "cwd": tmp,
                    })
            run.assert_not_called()

    def test_ask_schema_matches_frontier_budget_ceiling(self):
        mcp = load_mcp()
        tool = next(tool for tool in mcp.TOOLS if tool["name"] == "ambient_ask")
        self.assertEqual(
            tool["inputSchema"]["properties"]["max_tokens"]["maximum"],
            1_000_000,
        )

    def test_unknown_tool_is_mcp_error(self):
        mcp = load_mcp()
        response = mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        })
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("unknown Ambient tool", response["error"]["message"])

    def test_audit_small_validates_cwd(self):
        mcp = load_mcp()
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            with self.assertRaises(mcp.ToolInputError):
                mcp.call_tool("ambient_audit_small", {
                    "paths": ["a.py"],
                    "cwd": missing,
                })


if __name__ == "__main__":
    unittest.main()
