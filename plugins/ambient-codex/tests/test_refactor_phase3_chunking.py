"""Phase 3F contracts for pure chunk-packing primitives."""

import importlib
import unittest


class ChunkingTests(unittest.TestCase):
    def test_module_owns_density_and_chunk_packing(self):
        core = importlib.import_module("ambient_codex.chunking")
        self.assertEqual(core.__all__, ("density_factor", "pack_chunks"))

    def test_injected_definition_boundaries_keep_blocks_whole_when_possible(self):
        core = importlib.import_module("ambient_codex.chunking")
        text = "one\n" * 90 + "def useful():\n" + "two\n" * 90
        chunks = core.pack_chunks(
            [("example.py", text)], 500,
            break_lines=lambda label, value: {91})
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertTrue(any("def useful():" in chunk for chunk in chunks))
