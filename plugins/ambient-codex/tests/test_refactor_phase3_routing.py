"""Phase 3D contracts for pure automatic model routing."""

import importlib
import unittest


class RoutingTests(unittest.TestCase):
    def test_module_owns_auto_spec_and_candidate_selection(self):
        core = importlib.import_module("ambient_codex.routing")
        self.assertEqual(core.__all__, ("is_auto_model", "select_auto_model"))
        self.assertTrue(core.is_auto_model(" AUTO:largest "))
        self.assertFalse(core.is_auto_model("chosen/model"))

    def test_cheapest_selects_a_ready_visible_model_that_fits(self):
        core = importlib.import_module("ambient_codex.routing")
        catalog = [
            {"id": "small", "ready": True, "price": 1, "fits": False},
            {"id": "hidden", "ready": True, "price": 0, "fits": True},
            {"id": "best", "ready": True, "price": 2, "fits": True},
        ]
        selected = core.select_auto_model(
            "auto", catalog,
            is_ready=lambda item: item["ready"],
            is_hidden=lambda item: item["id"] == "hidden",
            context_length=lambda item: 10,
            output_price=lambda item: item["price"],
            fits=lambda item: item["fits"],
        )
        self.assertEqual(selected, ("best", "cheapest READY that fits"))
