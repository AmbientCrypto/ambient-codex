"""Phase 0 characterization for the extensionless CLI packaging seam.

These tests freeze the public process contract and prove that the minimal
internal package survives source loading, a copied Codex plugin root, and a real
Python package installation. No workflow implementation moves in this phase.
"""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
PACKAGE = ROOT / "ambient_codex" / "__init__.py"
PLUGIN_VERSION = json.loads(
    (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
)["version"].split("+", 1)[0]


def isolated_env(home):
    env = dict(os.environ)
    for name in (
        "AMBIENT_API_KEY",
        "AMBIENT_CODEX_API_KEY",
        "AMBIENT_API_URL",
        "AMBIENT_ALLOW_URL",
        "AMBIENT_ALLOW_INSECURE",
        "PLUGIN_ROOT",
        "PYTHONPATH",
    ):
        env.pop(name, None)
    env.update({
        "AMBIENT_CODEX_HOME": str(Path(home) / "state"),
        "AMBIENT_TELEMETRY": "off",
        "AMBIENT_FLEET_BUDGET": "off",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    return env


def run_cli(root, args, *, cwd, env):
    return subprocess.run(
        [sys.executable, str(Path(root) / "bin" / "ambient"), *args],
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def copy_plugin(source, destination):
    return Path(shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".coverage",
            "htmlcov",
            "build",
            "dist",
            "*.egg-info",
        ),
    ))


class SourceAndPluginCacheTests(unittest.TestCase):
    def test_extensionless_source_loader_bootstraps_internal_package(self):
        loader = importlib.machinery.SourceFileLoader(
            "ambient_phase0_source", str(BIN)
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)

        loader.exec_module(module)

        self.assertEqual(module.INTERNAL_PACKAGE_LAYOUT, 1)
        self.assertEqual(module.INTERNAL_PACKAGE_NAME, "ambient_codex")

    def test_copied_plugin_runs_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            copied = copy_plugin(ROOT, base / "cache" / "ambient-codex")
            work = base / "unrelated-worktree"
            work.mkdir()

            self.assertTrue((copied / "ambient_codex" / "__init__.py").is_file())
            proc = run_cli(
                copied,
                ["--version"],
                cwd=work,
                env=isolated_env(base / "home"),
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"ambient {PLUGIN_VERSION}")

    def test_offline_process_contract_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = isolated_env(base / "home")
            help_result = run_cli(ROOT, ["--help"], cwd=base, env=env)
            unknown = run_cli(ROOT, ["not-a-command"], cwd=base, env=env)
            control = run_cli(
                ROOT,
                ["control", "--offline", "--json"],
                cwd=base,
                env=env,
            )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in ("ask", "audit", "build", "agent", "control"):
            self.assertIn(command, help_result.stdout)
        self.assertEqual(unknown.returncode, 64)
        self.assertIn("invalid choice", unknown.stderr)
        self.assertEqual(control.returncode, 0, control.stderr)
        payload = json.loads(control.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["surface"], "codex-native")
        self.assertEqual(payload["version"], PLUGIN_VERSION)
        self.assertEqual(payload["mode"], "off")
        self.assertFalse(payload["key"]["configured"])
        self.assertIn("workflows", payload)
        self.assertIn("actions", payload)


class PackageMetadataTests(unittest.TestCase):
    def test_pyproject_explicitly_includes_internal_package(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertTrue(PACKAGE.is_file())
        self.assertIn('packages = ["ambient_codex"]', text)
        self.assertNotIn("packages = []", text)


@unittest.skipUnless(
    os.environ.get("AMBIENT_PACKAGING_TEST") == "1",
    "real package-install smoke is an explicit CI/local release gate",
)
class PackageInstallTests(unittest.TestCase):
    def _run(self, args, *, base, home, timeout=180, cwd=None):
        return subprocess.run(
            [str(item) for item in args],
            cwd=str(cwd or base),
            env=isolated_env(base / home),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def _create_venv(self, base):
        venv = base / "venv"
        create = self._run(
            [sys.executable, "-m", "venv", venv],
            base=base,
            home="create-home",
        )
        self.assertEqual(create.returncode, 0, create.stderr)
        scripts = venv / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        return scripts, python

    def _install(self, base, python, source):
        install = self._run(
            [python, "-m", "pip", "install", "--no-deps", "--no-compile", source],
            base=base,
            home="install-home",
        )
        self.assertEqual(install.returncode, 0, install.stderr)

    def test_isolated_venv_install_contains_package_and_working_script(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = copy_plugin(ROOT, base / "source")
            scripts, python = self._create_venv(base)
            self._install(base, python, source)

            probe = self._run(
                [python, "-c", "import ambient_codex"],
                base=base,
                home="probe-home",
                timeout=60,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            installed_script = scripts / "ambient"
            self.assertTrue(installed_script.is_file())
            work = base / "work"
            work.mkdir()
            proc = self._run(
                [python, installed_script, "--version"],
                base=base,
                home="runtime-home",
                timeout=60,
                cwd=work,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"ambient {PLUGIN_VERSION}")


if __name__ == "__main__":
    unittest.main()
