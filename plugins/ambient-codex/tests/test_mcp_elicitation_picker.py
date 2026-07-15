"""`ambient_pick_model` renders a native Codex picker via MCP `elicitation/create`.

Wire shapes here were captured from a real Codex 0.143.0 TUI driven under a pty:

    -> {"method":"elicitation/create","params":{"message":...,"requestedSchema":{...}}}
    <- {"id":...,"result":{"action":"accept","content":{"model":"z-ai/glm-5.2"}}}
    <- {"id":...,"result":{"action":"cancel"}}                      (user pressed esc)

Codex advertises `capabilities:{"elicitation":{}}` at initialize. Under `codex exec`
there is no human, so elicitations are auto-cancelled — every non-accept path must
collapse to "change nothing" rather than hang or loop.
"""
import importlib.util
import io
import json
import queue
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "mcp" / "ambient_mcp.py"

SERVING = [
    {"id": "z-ai/glm-5.2", "ready": True, "hidden": False},
    {"id": "deepseek/deepseek-v3", "ready": True, "hidden": False},
]


def load_mcp():
    spec = importlib.util.spec_from_file_location("ambient_mcp_picker", MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode_jsonl(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


class TestElicitationGate(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()

    def _session(self, *, caps, protocol="2025-06-18", streams=True):
        session = self.mcp.SESSION
        session.client_capabilities = caps
        session.protocol_version = protocol
        session.stdin = io.BytesIO() if streams else None
        session.stdout = io.BytesIO() if streams else None
        return session

    def test_codex_capabilities_enable_elicitation(self):
        self.assertTrue(self._session(caps={"elicitation": {}}).supports_elicitation())

    def test_missing_capability_disables_elicitation(self):
        self.assertFalse(self._session(caps={}).supports_elicitation())

    def test_protocol_older_than_2025_06_18_disables_elicitation(self):
        session = self._session(caps={"elicitation": {}}, protocol="2024-11-05")
        self.assertFalse(session.supports_elicitation())

    def test_initialize_records_capabilities_and_version(self):
        self.mcp.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18",
                       "capabilities": {"elicitation": {}}},
        })
        self.assertEqual(self.mcp.SESSION.protocol_version, "2025-06-18")
        self.assertEqual(self.mcp.SESSION.client_capabilities, {"elicitation": {}})

    def test_initialize_without_capabilities_does_not_crash(self):
        response = self.mcp.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.mcp.SESSION.client_capabilities, {})

    def test_elicit_returns_none_without_capability(self):
        self._session(caps={})
        self.assertIsNone(self.mcp.elicit("m", {"type": "object"}))


class TestElicitationChoice(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()

    def test_accept_yields_the_value(self):
        result = {"action": "accept", "content": {"model": "z-ai/glm-5.2"}}
        self.assertEqual(self.mcp.elicitation_choice(result, "model"), "z-ai/glm-5.2")

    def test_cancel_yields_nothing(self):
        self.assertIsNone(self.mcp.elicitation_choice({"action": "cancel"}, "model"))

    def test_decline_yields_nothing(self):
        self.assertIsNone(self.mcp.elicitation_choice({"action": "decline"}, "model"))

    def test_none_yields_nothing(self):
        self.assertIsNone(self.mcp.elicitation_choice(None, "model"))

    def test_accept_with_missing_field_yields_nothing(self):
        self.assertIsNone(
            self.mcp.elicitation_choice({"action": "accept", "content": {}}, "model"))


class TestPickModelTool(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()
        self.mcp.SESSION.client_capabilities = {"elicitation": {}}
        self.mcp.SESSION.protocol_version = "2025-06-18"
        self.mcp.SESSION.stdin = io.BytesIO()
        self.mcp.SESSION.stdout = io.BytesIO()

    def test_accept_persists_the_chosen_model_across_both_lanes_by_default(self):
        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", return_value={
                 "action": "accept", "content": {"model": "deepseek/deepseek-v3"}}), \
             mock.patch.object(self.mcp, "run_ambient",
                               return_value={"content": [], "isError": False}) as run:
            self.mcp.pick_model_tool({})
        run.assert_called_once_with(["control", "model", "deepseek/deepseek-v3"])

    def test_explicit_lane_is_applied_and_never_elicited(self):
        captured = {}

        def fake_elicit(message, schema, *a, **k):
            captured["schema"] = schema
            captured["message"] = message
            return {"action": "accept", "content": {"model": "z-ai/glm-5.2"}}

        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", side_effect=fake_elicit), \
             mock.patch.object(self.mcp, "run_ambient",
                               return_value={"content": [], "isError": False}) as run:
            self.mcp.pick_model_tool({"lane": "chat"})
        self.assertNotIn("lane", captured["schema"]["properties"])
        self.assertIn("chat", captured["message"])
        run.assert_called_once_with(["control", "model", "z-ai/glm-5.2", "--chat"])

    def test_picker_asks_exactly_one_question(self):
        """Codex does not preserve schema property order.

        A model+lane form rendered "Apply to" as field 1/2 in the real TUI, so a user
        who asked to switch models was quizzed about lanes first. One field, always.
        """
        captured = {}

        def fake_elicit(message, schema, *a, **k):
            captured["schema"] = schema
            return {"action": "cancel"}

        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", side_effect=fake_elicit):
            self.mcp.pick_model_tool({})
        self.assertEqual(list(captured["schema"]["properties"]), ["model"])
        self.assertEqual(captured["schema"]["required"], ["model"])

    def test_schema_uses_the_restricted_enum_shape(self):
        """`enum` + `enumNames` is the MCP restricted-subset enum shape.

        `oneOf: [{const, title}]` also renders in Codex but is a Codex extension a
        stricter client may reject, and both produce the same picker.
        """
        captured = {}

        def fake_elicit(message, schema, *a, **k):
            captured["schema"] = schema
            return {"action": "cancel"}

        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", side_effect=fake_elicit):
            self.mcp.pick_model_tool({})
        model = captured["schema"]["properties"]["model"]
        self.assertEqual(model["type"], "string")
        self.assertNotIn("oneOf", model)
        self.assertEqual(model["enum"], ["z-ai/glm-5.2", "deepseek/deepseek-v3"])
        self.assertEqual(len(model["enumNames"]), len(model["enum"]))
        self.assertTrue(all(model["enumNames"]))
        self.assertIn("model", captured["schema"]["required"])

    def test_cancel_changes_nothing(self):
        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", return_value={"action": "cancel"}), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_model_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("Model unchanged", text)
        self.assertIn("1. z-ai/glm-5.2", text)
        self.assertIn("Browse all models", text)
        self.assertIn("on-demand models", text)
        self.assertIn("ambient_set_model", text)

    def test_timeout_or_error_changes_nothing(self):
        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", return_value=None), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_model_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("Model unchanged", text)
        self.assertIn("1. z-ai/glm-5.2", text)
        self.assertIn("Browse all models", text)
        self.assertIn("ambient_models", text)
        self.assertIn("ambient_set_model", text)

    def test_a_model_we_never_offered_is_refused(self):
        """Never persist an id echoed back that was not in our own option list."""
        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "elicit", return_value={
                 "action": "accept", "content": {"model": "evil/injected", "lane": "both"}}), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_model_tool({})
        run.assert_not_called()
        self.assertTrue(out["isError"])

    def test_no_picker_capability_returns_a_numbered_text_menu(self):
        self.mcp.SESSION.client_capabilities = {}
        with mock.patch.object(self.mcp, "_serving_models", return_value=SERVING), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_model_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("1. z-ai/glm-5.2", text)
        self.assertIn("2. deepseek/deepseek-v3", text)
        self.assertIn("3. Browse all models", text)
        self.assertIn("ambient_set_model", text)

    def test_nothing_serving_is_explained_not_crashed(self):
        with mock.patch.object(self.mcp, "_serving_models", return_value=[]), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_model_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("serving", text)
        self.assertIn("spin up on demand", text)
        self.assertIn("all=true", text)

    def test_only_serving_unhidden_models_are_offered(self):
        catalogue = json.dumps({"schema_version": 1, "models": [
            {"id": "serving/a", "ready": True, "hidden": False},
            {"id": "cold/b", "ready": False, "hidden": False},
            {"id": "hidden/c", "ready": True, "hidden": True},
        ]})
        completed = subprocess.CompletedProcess([], 0, stdout=catalogue, stderr="")
        with mock.patch.object(self.mcp.subprocess, "run", return_value=completed):
            self.assertEqual([m["id"] for m in self.mcp._serving_models()], ["serving/a"])


class TestElicitStdioRoundTrip(unittest.TestCase):
    """Drive the real `elicit()` against Codex's captured reply shapes."""

    def setUp(self):
        self.mcp = load_mcp()
        self.mcp.SESSION.client_capabilities = {"elicitation": {}}
        self.mcp.SESSION.protocol_version = "2025-06-18"
        self.mcp.SESSION.framing = "jsonl"

    def _run(self, replies, timeout=10):
        stream = io.BytesIO(b"".join(encode_jsonl(r) for r in replies))
        self.mcp.SESSION.stdin = stream
        self.mcp.SESSION.stdout = io.BytesIO()
        self.mcp.SESSION.reader = self.mcp.MessageReader(stream)
        return self.mcp.elicit("pick", {"type": "object"}, timeout_seconds=timeout)

    def test_accept_round_trip(self):
        result = self._run([
            {"jsonrpc": "2.0", "id": "amb-elicit-1",
             "result": {"action": "accept", "content": {"model": "z-ai/glm-5.2"}}},
        ])
        self.assertEqual(result["action"], "accept")
        sent = json.loads(self.mcp.SESSION.stdout.getvalue().splitlines()[0])
        self.assertEqual(sent["method"], "elicitation/create")
        self.assertEqual(sent["id"], "amb-elicit-1")
        self.assertIn("requestedSchema", sent["params"])

    def test_cancel_round_trip(self):
        result = self._run([
            {"jsonrpc": "2.0", "id": "amb-elicit-1", "result": {"action": "cancel"}},
        ])
        self.assertEqual(result, {"action": "cancel"})

    def test_error_reply_is_treated_as_no_answer(self):
        result = self._run([
            {"jsonrpc": "2.0", "id": "amb-elicit-1",
             "error": {"code": -32601, "message": "unsupported"}},
        ])
        self.assertIsNone(result)

    def test_client_hangup_mid_picker_returns_none(self):
        self.assertIsNone(self._run([]))

    def test_interleaved_client_request_is_served_without_deadlock(self):
        """Codex may ping us while the human stares at the picker."""
        result = self._run([
            {"jsonrpc": "2.0", "id": 42, "method": "ping"},
            {"jsonrpc": "2.0", "id": "amb-elicit-1",
             "result": {"action": "accept", "content": {"model": "z-ai/glm-5.2"}}},
        ])
        self.assertEqual(result["action"], "accept")
        lines = self.mcp.SESSION.stdout.getvalue().splitlines()
        replies = [json.loads(line) for line in lines]
        self.assertEqual(replies[0]["method"], "elicitation/create")
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 42, "result": {}})

    def test_batched_write_is_not_missed(self):
        """Two messages in one write must both be seen.

        A `select()`-based wait polls the fd and cannot see a message already sitting
        in the BufferedReader, so a batched write would stall the picker until timeout.
        """
        blob = (encode_jsonl({"jsonrpc": "2.0", "id": 7, "method": "ping"})
                + encode_jsonl({"jsonrpc": "2.0", "id": "amb-elicit-1",
                                "result": {"action": "accept",
                                           "content": {"model": "z-ai/glm-5.2"}}}))
        stream = io.BytesIO(blob)
        self.mcp.SESSION.stdin = stream
        self.mcp.SESSION.stdout = io.BytesIO()
        self.mcp.SESSION.reader = self.mcp.MessageReader(stream)
        result = self.mcp.elicit("pick", {"type": "object"}, timeout_seconds=10)
        self.assertEqual(result["action"], "accept")

    def test_late_reply_to_an_abandoned_picker_is_dropped(self):
        result = self._run([
            {"jsonrpc": "2.0", "id": "amb-elicit-999", "result": {"action": "accept"}},
            {"jsonrpc": "2.0", "id": "amb-elicit-1", "result": {"action": "cancel"}},
        ])
        self.assertEqual(result, {"action": "cancel"})

    def test_timeout_when_nobody_ever_answers(self):
        class SilentReader:
            """A client that renders the picker and then never replies."""

            def get(self, timeout=None):
                raise queue.Empty

        self.mcp.SESSION.stdin = io.BytesIO()
        self.mcp.SESSION.stdout = io.BytesIO()
        self.mcp.SESSION.reader = SilentReader()
        self.assertIsNone(self.mcp.elicit("pick", {"type": "object"}, timeout_seconds=1))


class TestServeIgnoresStrayResponses(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()

    def test_is_response_discriminates_requests_from_replies(self):
        self.assertTrue(self.mcp.is_response({"id": 1, "result": {}}))
        self.assertTrue(self.mcp.is_response({"id": 1, "error": {"code": -1}}))
        self.assertFalse(self.mcp.is_response({"id": 1, "method": "ping"}))
        self.assertFalse(self.mcp.is_response({"method": "notifications/initialized"}))

    def test_server_never_answers_a_stray_response(self):
        """Replying to a reply would put a bogus error response on the wire."""
        payload = encode_jsonl({"jsonrpc": "2.0", "id": "amb-elicit-1",
                                "result": {"action": "cancel"}})
        proc = subprocess.run(
            [sys.executable, str(MCP)], input=payload,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stdout, b"", "server answered a stray response")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestPickModeTool(unittest.TestCase):
    def setUp(self):
        self.mcp = load_mcp()
        self.mcp.SESSION.client_capabilities = {"elicitation": {}}
        self.mcp.SESSION.protocol_version = "2025-06-18"
        self.mcp.SESSION.stdout = io.BytesIO()

    def test_accept_arms_the_ambient_session_without_persisting_config(self):
        with mock.patch.object(self.mcp, "elicit",
                               return_value={"action": "accept",
                                             "content": {"state": "takeover"}}), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_mode_tool({})
        run.assert_not_called()
        self.assertEqual(self.mcp.SESSION.mode, "takeover")
        self.assertIn("Ambient session is ON", out["content"][0]["text"])

    def test_schema_offers_the_three_modes(self):
        captured = {}

        def fake(msg, schema, *a, **k):
            captured["schema"] = schema
            return {"action": "cancel"}

        self.mcp.SESSION.mode = "on"
        with mock.patch.object(self.mcp, "elicit", side_effect=fake):
            self.mcp.pick_mode_tool({})
        self.assertEqual(captured["schema"]["properties"]["state"]["enum"],
                         ["off", "on", "takeover"])
        self.assertEqual(captured["schema"]["properties"]["state"]["enumNames"][2],
                         "Ambient session")

    def test_cancel_changes_nothing(self):
        self.mcp.SESSION.mode = "on"
        with mock.patch.object(self.mcp, "elicit", return_value={"action": "cancel"}), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_mode_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("Mode unchanged", text)
        self.assertIn("1. Normal Codex", text)
        self.assertIn("2. Delegate", text)
        self.assertIn("3. Ambient session", text)
        self.assertIn("ambient_set_mode", text)

    def test_no_picker_returns_a_numbered_menu(self):
        self.mcp.SESSION.client_capabilities = {}
        with mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_mode_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("1. Normal Codex", text)
        self.assertIn("ambient_set_mode", text)

    def test_timeout_or_auto_cancel_returns_the_numbered_menu(self):
        with mock.patch.object(self.mcp, "elicit", return_value=None), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_mode_tool({})
        run.assert_not_called()
        text = out["content"][0]["text"]
        self.assertIn("Mode unchanged", text)
        self.assertIn("1. Normal Codex", text)
        self.assertIn("ambient_set_mode", text)

    def test_a_bogus_state_is_refused(self):
        with mock.patch.object(self.mcp, "elicit",
                               return_value={"action": "accept",
                                             "content": {"state": "evil"}}), \
             mock.patch.object(self.mcp, "run_ambient") as run:
            out = self.mcp.pick_mode_tool({})
        run.assert_not_called()
        self.assertTrue(out["isError"])
