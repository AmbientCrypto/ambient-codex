"""Safe local-marketplace staging and rollback tests."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "scripts" / "install_plugin_atomic.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("ambient_atomic_installer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_marketplace(root, version="1.0.1"):
    plugin = root / "plugins" / "ambient-codex"
    (root / ".agents" / "plugins").mkdir(parents=True)
    plugin.joinpath(".codex-plugin").mkdir(parents=True)
    plugin.joinpath("bin").mkdir()
    plugin.joinpath("mcp").mkdir()
    (root / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "ambient-codex",
                "plugins": [
                    {
                        "name": "ambient-codex",
                        "source": {
                            "source": "local",
                            "path": "./plugins/ambient-codex",
                        },
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_USE",
                        },
                        "category": "Productivity",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "ambient-codex", "version": version}), encoding="utf-8"
    )
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "ambient-codex"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (plugin / "bin" / "ambient").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (plugin / "mcp" / "ambient_mcp.py").write_text(
        f'SERVER_VERSION = "{version}"\n', encoding="utf-8"
    )
    return plugin


class AtomicStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_installer()

    def test_stage_is_atomic_version_synchronized_and_source_preserving(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            destination = base / "installed"
            source_plugin = seed_marketplace(source)
            source_manifest_before = (
                source_plugin / ".codex-plugin" / "plugin.json"
            ).read_bytes()

            result = self.installer.stage_marketplace(
                source, destination, cachebuster="test-123", validator=lambda _path: None
            )

            self.assertEqual(result.version, "1.0.1+codex.test-123")
            self.assertEqual(
                (source_plugin / ".codex-plugin" / "plugin.json").read_bytes(),
                source_manifest_before,
            )
            staged_plugin = destination / "plugins" / "ambient-codex"
            values = self.installer.read_synchronized_versions(staged_plugin)
            self.assertEqual(set(values.values()), {"1.0.1+codex.test-123"})
            self.assertFalse(any(path.name == ".git" for path in destination.rglob(".git")))

    def test_failed_validation_keeps_previous_destination_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            destination = base / "installed"
            seed_marketplace(source)
            destination.mkdir()
            marker = destination / "keep.txt"
            marker.write_text("old-good-install", encoding="utf-8")

            def reject(_path):
                raise ValueError("validation failed")

            with self.assertRaisesRegex(ValueError, "validation failed"):
                self.installer.stage_marketplace(
                    source, destination, cachebuster="bad", validator=reject
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "old-good-install")

    def test_rejects_destination_inside_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            seed_marketplace(source)

            with self.assertRaisesRegex(ValueError, "outside the source"):
                self.installer.stage_marketplace(
                    source,
                    source / "nested-install",
                    cachebuster="unsafe",
                    validator=lambda _path: None,
                )


class AtomicInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_installer()

    def test_successful_switch_verifies_expected_version(self):
        calls = []

        def runner(argv):
            calls.append(tuple(argv))
            if argv[:3] == ["codex", "plugin", "list"]:
                return {
                    "installed": [
                        {
                            "pluginId": "ambient-codex@ambient-codex",
                            "version": "1.0.1+codex.test-123",
                            "installed": True,
                        }
                    ]
                }
            return {"status": "ok"}

        self.installer.switch_marketplace(
            Path("/safe/local-marketplace"),
            expected_version="1.0.1+codex.test-123",
            runner=runner,
        )

        self.assertEqual(
            calls[:4],
            [
                ("codex", "plugin", "remove", "ambient-codex@ambient-codex", "--json"),
                ("codex", "plugin", "marketplace", "remove", "ambient-codex", "--json"),
                ("codex", "plugin", "marketplace", "add", "/safe/local-marketplace", "--json"),
                ("codex", "plugin", "add", "ambient-codex@ambient-codex", "--json"),
            ],
        )

    def test_failed_switch_rolls_back_to_remote_marketplace(self):
        calls = []

        def runner(argv):
            calls.append(tuple(argv))
            if argv[:4] == ["codex", "plugin", "add", "ambient-codex@ambient-codex"]:
                raise RuntimeError("install failed")
            return {"status": "ok"}

        with self.assertRaisesRegex(RuntimeError, "install failed"):
            self.installer.switch_marketplace(
                Path("/safe/local-marketplace"),
                expected_version="1.0.1+codex.test-123",
                runner=runner,
                rollback_source="https://github.com/AmbientCrypto/ambient-codex.git",
                rollback_ref="v1",
            )

        self.assertIn(
            (
                "codex",
                "plugin",
                "marketplace",
                "add",
                "https://github.com/AmbientCrypto/ambient-codex.git",
                "--ref",
                "v1",
                "--json",
            ),
            calls,
        )
        self.assertEqual(
            calls[-1],
            ("codex", "plugin", "add", "ambient-codex@ambient-codex", "--json"),
        )


if __name__ == "__main__":
    unittest.main()
