import json
import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parents[1]


class ReleaseReadinessTests(unittest.TestCase):
    def test_public_readme_is_short_official_v1_install_guide(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 180)
        self.assertIn("Official Ambient", text)
        self.assertIn("V1", text)
        self.assertNotIn("Beta", text)
        self.assertIn("codex plugin marketplace add", text)
        self.assertIn("codex plugin add ambient-codex@ambient-codex", text)
        self.assertIn("`$ambient`", text)
        self.assertIn("docs/INSTALL.md", text)
        self.assertIn("docs/FEATURES.md", text)

    def test_install_and_feature_guides_are_present_and_concise(self):
        install = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        features = (REPO_ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(install.splitlines()), 160)
        self.assertLessEqual(len(features.splitlines()), 180)
        self.assertIn("ambient-codex setup", install)
        self.assertIn("Normal Codex", features)
        self.assertIn("Delegate", features)
        self.assertIn("Ambient session", features)

    def test_packaged_readme_is_concise_and_v1_labeled(self):
        text = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 180)
        self.assertIn("Official Ambient", text)
        self.assertIn("V1", text)
        self.assertNotIn("Beta", text)
        self.assertIn("app.ambient.xyz", text)

    def test_public_metadata_uses_official_ambient_v1_branding(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"))
        marketplace = json.loads(
            (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"))
        skill_metadata = (
            PLUGIN_ROOT / "skills" / "ambient" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertEqual(manifest["author"]["name"], "Ambient")
        self.assertEqual(manifest["interface"]["developerName"], "Ambient")
        self.assertEqual(manifest["interface"]["displayName"], "Ambient Codex")
        self.assertEqual(marketplace["interface"]["displayName"], "Ambient Codex")
        self.assertIn('display_name: "Ambient Codex"', skill_metadata)
        self.assertNotIn("Beta", json.dumps(manifest))
        self.assertNotIn("Beta", json.dumps(marketplace))
        self.assertNotIn("Beta", skill_metadata)
        self.assertEqual(manifest["version"], "1.0.1")
        self.assertEqual(manifest["author"]["url"], "https://ambient.xyz")

    def test_repository_has_github_community_files(self):
        expected = (
            "LICENSE",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
        )
        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((REPO_ROOT / relative).is_file())
        license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)

    def test_github_actions_are_immutable_and_security_scanning_is_configured(self):
        workflows = list((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertTrue((REPO_ROOT / ".github" / "workflows" / "codeql.yml").is_file())
        self.assertTrue((REPO_ROOT / ".github" / "dependabot.yml").is_file())
        use_pattern = re.compile(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)")
        immutable = re.compile(r"^[^@]+@[0-9a-f]{40}$")
        for workflow in workflows:
            for action in use_pattern.findall(workflow.read_text(encoding="utf-8")):
                with self.subTest(workflow=workflow.name, action=action):
                    self.assertRegex(action, immutable)

    def test_live_release_tools_are_not_in_the_installed_plugin(self):
        self.assertFalse((PLUGIN_ROOT / "tools").exists())
        self.assertTrue((REPO_ROOT / "tools" / "stress_test.sh").is_file())
        self.assertTrue((REPO_ROOT / "tools" / "model_matrix.sh").is_file())

    def test_current_threat_model_has_no_removed_runtime_controls(self):
        text = (REPO_ROOT / "ambient-codex-threat-model.md").read_text(
            encoding="utf-8")
        for removed in (
            "spend gates",
            "spend reservations",
            "fleet reservations",
            "spend ceiling",
            "`--allow-cost`",
            "`estimate_cost`",
            "`_gate_amount`",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, text)

    def test_public_markdown_has_no_personal_home_path(self):
        markdown = list(REPO_ROOT.glob("*.md"))
        markdown.extend((REPO_ROOT / "docs").glob("*.md"))
        markdown.extend(PLUGIN_ROOT.glob("*.md"))
        markdown.extend((PLUGIN_ROOT / "docs").glob("*.md"))
        for path in markdown:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"/Users/[^/\s]+", text))


if __name__ == "__main__":
    unittest.main()
