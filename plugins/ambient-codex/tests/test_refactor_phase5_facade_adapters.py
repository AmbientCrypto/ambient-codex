"""Contracts for compact, late-bound CLI facade adapters."""

import importlib
import unittest
from types import SimpleNamespace


class FacadeAdapterTests(unittest.TestCase):
    def test_adapter_resolves_module_and_dependencies_at_call_time(self):
        core = importlib.import_module("ambient_codex.facade_adapters")
        namespace = {
            "_feature": SimpleNamespace(run=lambda value, deps: (value, deps)),
            "_deps": lambda: "first",
        }
        core.install(namespace, "_feature", "_deps", "public=run")

        self.assertEqual(namespace["public"]("value"), ("value", "first"))
        namespace["_deps"] = lambda: "second"
        namespace["_feature"] = SimpleNamespace(
            run=lambda value, deps: (value.upper(), deps))
        self.assertEqual(namespace["public"]("value"), ("VALUE", "second"))

    def test_invalid_adapter_spec_fails_fast(self):
        core = importlib.import_module("ambient_codex.facade_adapters")
        with self.assertRaises(ValueError):
            core.install({}, "module", "deps", "not valid!")

    def test_dependency_binding_resolves_dotted_sources_late(self):
        core = importlib.import_module("ambient_codex.facade_adapters")
        namespace = {
            "constant": 1,
            "feature": SimpleNamespace(callback=lambda: "first"),
        }
        def factory(**values):
            return values

        first = core.bind(
            namespace, factory, "value=constant action=feature.callback")
        namespace["constant"] = 2
        namespace["feature"] = SimpleNamespace(callback=lambda: "second")
        second = core.bind(
            namespace, factory, "value=constant action=feature.callback")

        self.assertEqual(first["value"], 1)
        self.assertEqual(first["action"](), "first")
        self.assertEqual(second["value"], 2)
        self.assertEqual(second["action"](), "second")


if __name__ == "__main__":
    unittest.main()
