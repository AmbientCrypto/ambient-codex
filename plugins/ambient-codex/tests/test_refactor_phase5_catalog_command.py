"""Phase 5 contracts for extracted model catalog commands."""

import importlib
import unittest


class CatalogCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.catalog_command")

    def test_dependency_bindings_are_immutable(self):
        deps = self.core.CatalogDependencies.bind(fetch_models=object())

        with self.assertRaises(TypeError):
            deps.bindings["fetch_models"] = object()

    def test_catalog_alias_is_removed_only_when_primary_is_present(self):
        rows = [
            {"id": "ambient/large"},
            {"id": "zai-org/GLM-5.1-FP8"},
        ]

        self.assertEqual(
            self.core.dedupe_catalog(rows), [{"id": "ambient/large"}])
        self.assertEqual(
            self.core.dedupe_catalog(rows[1:]), rows[1:])


if __name__ == "__main__":
    unittest.main()
