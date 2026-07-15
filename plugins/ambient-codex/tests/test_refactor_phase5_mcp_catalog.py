"""Contracts for the extracted native MCP tool catalog."""

import importlib
import unittest


class McpCatalogTests(unittest.TestCase):
    def test_catalog_has_fourteen_unique_strict_tools(self):
        catalog = importlib.import_module("mcp.ambient_mcp_catalog")
        names = [tool["name"] for tool in catalog.TOOLS]

        self.assertEqual(len(names), 14)
        self.assertEqual(len(set(names)), 14)
        for tool in catalog.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
