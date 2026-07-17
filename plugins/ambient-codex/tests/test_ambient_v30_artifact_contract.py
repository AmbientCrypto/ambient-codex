"""Declarative Ambient artifact contract and provenance regression tests."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "ambient" / "scripts" / "artifact_contract.py"


def load_contract_module():
    spec = importlib.util.spec_from_file_location("ambient_artifact_contract", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_deck_spec():
    return {
        "ambient_artifact_version": 1,
        "artifact": {
            "kind": "deck",
            "title": "Synthetic launch deck",
            "scenario": {"fictional": True},
            "outputs": ["output/launch-deck.pptx"],
        },
        "provenance": {
            "creative_author": "Ambient",
            "creative_authorship": "ambient-only",
            "model": "example/model",
            "prompt_sha256": "a" * 64,
            "codex_roles": [
                "orchestration",
                "verification",
                "deterministic-rendering",
            ],
        },
        "rendering": {
            "engine": "example-renderer",
            "engine_version": "1.2.3",
            "font_policy": "embed-or-fail",
            "asset_policy": "local-only",
        },
        "content": {
            "slide_size": {"width": 1280, "height": 720},
            "theme": {"background": "#071019", "foreground": "#F7F4EC"},
            "slides": [
                {
                    "id": "title",
                    "title": "The Wind Layer",
                    "speaker_notes": "State that every company and result is fictional.",
                    "elements": [
                        {
                            "type": "text",
                            "text": "Wind, routed like flight control.",
                            "x": 64,
                            "y": 80,
                            "width": 800,
                            "height": 160,
                        },
                        {
                            "type": "asset",
                            "asset": "assets/hero-kite.svg",
                            "x": 900,
                            "y": 80,
                            "width": 300,
                            "height": 500,
                        },
                    ],
                }
            ],
        },
    }


def valid_site_spec():
    return {
        "ambient_artifact_version": 1,
        "artifact": {
            "kind": "site",
            "title": "Synthetic campaign site",
            "scenario": {"fictional": True},
            "outputs": ["public/index.html", "public/styles.css"],
        },
        "provenance": copy.deepcopy(valid_deck_spec()["provenance"]),
        "rendering": copy.deepcopy(valid_deck_spec()["rendering"]),
        "content": {
            "entrypoint": "public/index.html",
            "files": [
                {
                    "path": "public/index.html",
                    "media_type": "text/html",
                    "source": "<main><h1>Synthetic launch</h1></main>",
                },
                {
                    "path": "public/styles.css",
                    "media_type": "text/css",
                    "source": "main { color: #071019; }",
                },
            ],
        },
    }


def valid_bundle_spec():
    deck = valid_deck_spec()
    site = valid_site_spec()
    return {
        "ambient_artifact_version": 1,
        "artifact": {
            "kind": "campaign_bundle",
            "title": "Synthetic launch bundle",
            "scenario": {"fictional": True},
            "outputs": deck["artifact"]["outputs"] + site["artifact"]["outputs"],
        },
        "provenance": copy.deepcopy(deck["provenance"]),
        "rendering": copy.deepcopy(deck["rendering"]),
        "content": {"artifacts": [deck, site]},
    }


def valid_document_spec():
    document = valid_deck_spec()
    document["artifact"] = {
        "kind": "document",
        "title": "Synthetic one-pager",
        "scenario": {"fictional": True},
        "outputs": ["output/one-pager.pdf"],
    }
    document["content"] = {
        "page_size": {"width": 792, "height": 612},
        "pages": [
            {
                "id": "page-1",
                "title": "Synthetic launch",
                "elements": [
                    {
                        "type": "text",
                        "text": "A fictional product launch.",
                        "x": 48,
                        "y": 48,
                        "width": 696,
                        "height": 80,
                    }
                ],
            }
        ],
    }
    return document


class ArtifactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract_module()

    def test_valid_deck_is_accepted_without_mutating_input(self):
        document = valid_deck_spec()
        before = copy.deepcopy(document)

        issues = self.contract.validate_artifact(document)

        self.assertEqual(issues, ())
        self.assertEqual(document, before)

    def test_rejects_non_ambient_or_ambiguous_creative_provenance(self):
        for field, value in (
            ("creative_author", "Codex"),
            ("creative_authorship", "mixed"),
            ("model", ""),
            ("prompt_sha256", "not-a-hash"),
        ):
            with self.subTest(field=field):
                document = valid_deck_spec()
                document["provenance"][field] = value
                issues = self.contract.validate_artifact(document)
                self.assertTrue(any(field in issue for issue in issues), issues)

    def test_rejects_unsafe_paths_external_assets_and_duplicate_outputs(self):
        document = valid_deck_spec()
        document["artifact"]["outputs"] = ["../escape.pptx", "../escape.pptx"]
        document["content"]["slides"][0]["elements"][1]["asset"] = (
            "https://example.invalid/tracker.png"
        )

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("outputs" in issue and "safe relative" in issue for issue in issues), issues)
        self.assertTrue(any("duplicate" in issue for issue in issues), issues)
        self.assertTrue(any("external" in issue for issue in issues), issues)

    def test_rejects_out_of_bounds_or_non_finite_geometry(self):
        document = valid_deck_spec()
        element = document["content"]["slides"][0]["elements"][0]
        element["x"] = float("nan")
        element["width"] = 5000

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("finite" in issue for issue in issues), issues)
        self.assertTrue(any("bounds" in issue for issue in issues), issues)

    def test_requires_unique_slide_ids_and_speaker_notes(self):
        document = valid_deck_spec()
        duplicate = copy.deepcopy(document["content"]["slides"][0])
        duplicate["speaker_notes"] = ""
        document["content"]["slides"].append(duplicate)

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("duplicate slide id" in issue for issue in issues), issues)
        self.assertTrue(any("speaker_notes" in issue for issue in issues), issues)

    def test_validates_every_nested_artifact_in_a_campaign_bundle(self):
        document = valid_bundle_spec()
        document["content"]["artifacts"][1]["content"]["files"][0]["path"] = (
            "../escaped.html"
        )

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("content.artifacts[1]" in issue for issue in issues), issues)
        self.assertTrue(any("safe relative" in issue for issue in issues), issues)

    def test_bundle_outputs_must_exactly_match_nested_outputs(self):
        document = valid_bundle_spec()
        document["artifact"]["outputs"].append("output/unowned.pdf")

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("exactly match nested artifact outputs" in issue for issue in issues), issues)

    def test_site_contract_requires_typed_source_files_and_matching_entrypoint(self):
        document = valid_site_spec()
        document["content"]["entrypoint"] = "public/missing.html"
        document["content"]["files"][1]["source"] = ""

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("entrypoint" in issue for issue in issues), issues)
        self.assertTrue(any("source must be non-empty" in issue for issue in issues), issues)

    def test_document_contract_requires_bounded_pages(self):
        document = valid_document_spec()
        document["content"]["pages"][0]["elements"][0]["height"] = 900

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("geometry exceeds page bounds" in issue for issue in issues), issues)

    def test_multi_model_provenance_requires_per_output_generation_records(self):
        document = valid_deck_spec()
        document["provenance"]["models"] = ["example/model", "fallback/model"]

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("generation_records" in issue for issue in issues), issues)

        document["provenance"]["generation_records"] = [
            {
                "model": "fallback/model",
                "prompt_sha256": "b" * 64,
                "outputs": ["output/launch-deck.pptx"],
            }
        ]
        self.assertEqual(self.contract.validate_artifact(document), ())

    def test_requires_reproducible_renderer_metadata(self):
        document = valid_deck_spec()
        document["rendering"].pop("engine_version")

        issues = self.contract.validate_artifact(document)

        self.assertTrue(any("rendering.engine_version" in issue for issue in issues), issues)

    def test_prompt_hash_uses_exact_bytes_without_newline_normalization(self):
        self.assertNotEqual(
            self.contract.prompt_sha256(b"brief\n"),
            self.contract.prompt_sha256(b"brief\r\n"),
        )

    def test_manifest_hashes_spec_and_rendered_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = root / "deck.json"
            output_path = root / "output" / "launch-deck.pptx"
            output_path.parent.mkdir()
            spec_path.write_text(json.dumps(valid_deck_spec()), encoding="utf-8")
            output_path.write_bytes(b"deterministic-pptx-placeholder")

            manifest = self.contract.build_manifest(spec_path, root)

            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["creative_author"], "Ambient")
            self.assertEqual(manifest["model"], "example/model")
            self.assertEqual(manifest["models"], ["example/model"])
            self.assertEqual(manifest["rendering"]["engine_version"], "1.2.3")
            self.assertEqual(
                manifest["spec_sha256"], hashlib.sha256(spec_path.read_bytes()).hexdigest()
            )
            self.assertEqual(
                manifest["outputs"][0]["sha256"],
                hashlib.sha256(output_path.read_bytes()).hexdigest(),
            )
            self.assertNotIn("created_at", manifest)

    def test_manifest_fails_closed_when_an_output_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = root / "deck.json"
            spec_path.write_text(json.dumps(valid_deck_spec()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing rendered output"):
                self.contract.build_manifest(spec_path, root)

    def test_schema_asset_and_skill_reference_are_packaged(self):
        schema = ROOT / "skills" / "ambient" / "assets" / "artifact-spec-v1.schema.json"
        reference = ROOT / "skills" / "ambient" / "references" / "artifact-contract.md"
        renderer = ROOT / "skills" / "ambient" / "scripts" / "render_deck.mjs"
        skill = (ROOT / "skills" / "ambient" / "SKILL.md").read_text(encoding="utf-8")

        self.assertTrue(schema.is_file())
        self.assertTrue(reference.is_file())
        self.assertTrue(renderer.is_file())
        self.assertIn("artifact_contract.py", skill)
        self.assertIn("artifact-contract.md", skill)


if __name__ == "__main__":
    unittest.main()
