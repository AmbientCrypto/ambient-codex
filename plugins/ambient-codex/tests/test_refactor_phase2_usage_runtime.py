"""Phase 2 contracts for extracted usage receipt and lock composition."""

import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


class UsageRuntimeTests(unittest.TestCase):
    def test_dependency_bindings_are_immutable(self):
        core = importlib.import_module("ambient_codex.usage_runtime")
        deps = core.UsageRuntimeDependencies.bind(usage_pricing=object())

        with self.assertRaises(TypeError):
            deps.bindings["usage_pricing"] = object()

    def test_default_savings_lookup_keeps_dependencies_during_config_read(self):
        core = importlib.import_module("ambient_codex.usage_runtime")
        set_cache = Mock()
        deps = core.UsageRuntimeDependencies.bind(
            get_savings_cache=Mock(return_value=None),
            os=SimpleNamespace(environ={}),
            read_config_file=Mock(return_value={"AMBIENT_SAVINGS": "on"}),
            set_savings_cache=set_cache,
        )

        self.assertTrue(core._savings_enabled(deps=deps))
        set_cache.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
