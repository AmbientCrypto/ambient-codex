"""Contracts for the extracted native MCP tool-handler surface."""

import importlib
import unittest


class McpToolHandlerTests(unittest.TestCase):
    def test_bound_handler_preserves_late_facade_replacement(self):
        handlers = importlib.import_module("mcp.ambient_mcp_tool_handlers")
        namespace = {
            "reject_unknown": lambda args, allowed: None,
            "run_ambient": lambda argv: {"argv": argv, "version": 1},
        }
        (status_tool,) = handlers.build(namespace, "status_tool")

        self.assertEqual(status_tool({})["version"], 1)
        namespace["run_ambient"] = lambda argv: {"argv": argv, "version": 2}
        self.assertEqual(status_tool({})["version"], 2)

    def test_unknown_handler_name_is_rejected(self):
        handlers = importlib.import_module("mcp.ambient_mcp_tool_handlers")
        with self.assertRaises(ValueError):
            handlers.build({}, "not_a_handler")


if __name__ == "__main__":
    unittest.main()
