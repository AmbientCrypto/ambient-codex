"""Ambient Codex must never share mutable state with another Ambient install.

Through 1.5.x this fork wrote `~/.config/ambient/env` — the exact path the Claude
plugin reads — so `ambient control mode takeover` here flipped Claude into takeover
on its next session, and a cheap model chosen here silently became Claude's default.
`test_codex_native_isolation.py` only asserted code-path isolation, so it stayed
green the whole time. These tests assert the *state* boundary instead.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parents[1]
CLI = ROOT / "bin" / "ambient"
CREDENTIALS = ROOT / "ambient_codex" / "credentials.py"
STRESS_HARNESS = REPO_ROOT / "tools" / "stress_test.sh"
MODEL_MATRIX = REPO_ROOT / "tools" / "model_matrix.sh"
STABLE_LAUNCHER = ROOT / "scripts" / "ambient-codex-launcher.py"

# `posixpath.expanduser` reads HOME; `ntpath.expanduser` ignores it and reads
# USERPROFILE. Sandboxing only HOME sent these tests at the developer's real
# profile on Windows.
_HOME_VARS = ("HOME", "USERPROFILE")


def sha1_file(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def sandbox_env(home, **extra):
    """A child-process env whose `~` resolves to `home` on every platform."""
    env = {**os.environ, "AMBIENT_NO_ONBOARD": "1", **extra}
    for var in _HOME_VARS:
        env[var] = home
    env.pop("AMBIENT_CODEX_HOME", None)
    # HOMEDRIVE/HOMEPATH are ntpath's fallback when USERPROFILE is unset.
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    return env


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def under(path, root):
    root = os.path.normcase(os.path.normpath(root))
    path = os.path.normcase(os.path.normpath(path))
    return path.startswith(root + os.sep)


def load_cli(home):
    """Import bin/ambient with `~` and AMBIENT_CODEX_HOME pointed at a sandbox."""
    prior = {k: os.environ.get(k)
             for k in (*_HOME_VARS, "AMBIENT_CODEX_HOME", "HOMEDRIVE", "HOMEPATH")}
    for var in _HOME_VARS:
        os.environ[var] = home
    for var in ("AMBIENT_CODEX_HOME", "HOMEDRIVE", "HOMEPATH"):
        os.environ.pop(var, None)
    try:
        spec = importlib.util.spec_from_loader(
            "ambient_cli_isolation",
            importlib.machinery.SourceFileLoader("ambient_cli_isolation", str(CLI)),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestStateRootIsolation(unittest.TestCase):
    def test_every_state_path_lives_under_the_codex_root(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            root = os.path.join(home, ".config", "ambient-codex")
            self.assertTrue(same_path(cli.STATE_DIR, root),
                            f"{cli.STATE_DIR} != {root}")
            for name in ("CONFIG_PATH", "USAGE_PATH", "CAPABILITY_PATH", "CACHE_DIR"):
                path = getattr(cli, name)
                self.assertTrue(under(path, root),
                                f"{name}={path} escapes the Codex state root")

    def test_no_state_path_touches_the_shared_ambient_dir(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            shared = os.path.join(home, ".config", "ambient")
            for name in ("CONFIG_PATH", "USAGE_PATH", "CAPABILITY_PATH", "CACHE_DIR"):
                path = getattr(cli, name)
                self.assertFalse(
                    same_path(path, shared) or under(path, shared),
                    f"{name}={path} lands inside the other install's dir {shared}",
                )

    def test_keychain_item_is_distinct_from_the_other_install(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            self.assertEqual(cli.KEYCHAIN_SERVICE, "ambient-codex")
            self.assertNotEqual(cli.KEYCHAIN_SERVICE, "ambient.xyz")

    def test_state_root_is_overridable(self):
        with tempfile.TemporaryDirectory() as home:
            override = os.path.join(home, "elsewhere")
            os.environ["AMBIENT_CODEX_HOME"] = override
            try:
                spec = importlib.util.spec_from_loader(
                    "ambient_cli_override",
                    importlib.machinery.SourceFileLoader(
                        "ambient_cli_override", str(CLI)),
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertTrue(same_path(module.STATE_DIR, override))
                self.assertTrue(same_path(module.CONFIG_PATH,
                                          os.path.join(override, "env")))
            finally:
                os.environ.pop("AMBIENT_CODEX_HOME", None)


class TestStateRootCannotBeAimedAtAnotherInstall(unittest.TestCase):
    """`AMBIENT_CODEX_HOME` relocates this install's state; it must not hijack another's.

    Without a guard, `AMBIENT_CODEX_HOME=~/.config/ambient` made this install read the
    other install's key and rewrite its delegate mode.
    """

    def _load_with(self, home, override):
        prior = {k: os.environ.get(k) for k in (*_HOME_VARS, "AMBIENT_CODEX_HOME")}
        for var in _HOME_VARS:
            os.environ[var] = home
        os.environ["AMBIENT_CODEX_HOME"] = override
        try:
            spec = importlib.util.spec_from_loader(
                "ambient_cli_guard",
                importlib.machinery.SourceFileLoader("ambient_cli_guard", str(CLI)))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_pointing_at_the_other_installs_root_is_refused(self):
        with tempfile.TemporaryDirectory() as home:
            other = os.path.join(home, ".config", "ambient")
            os.makedirs(other)
            with self.assertRaises(SystemExit) as cm:
                self._load_with(home, other)
            self.assertIn("belongs to another Ambient install", str(cm.exception))

    def test_pointing_at_a_foreign_config_is_refused(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = os.path.join(home, "someone-elses")
            os.makedirs(foreign)
            with open(os.path.join(foreign, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_API_KEY=sk-not-ours\n")
            with self.assertRaises(SystemExit) as cm:
                self._load_with(home, foreign)
            self.assertIn("did not create", str(cm.exception))

    def test_a_fresh_override_dir_is_accepted_and_claimed(self):
        with tempfile.TemporaryDirectory() as home:
            mine = os.path.join(home, "mine")
            proc = subprocess.run(
                [sys.executable, str(CLI), "control", "mode", "on"],
                env={**sandbox_env(home), "AMBIENT_CODEX_HOME": mine},
                capture_output=True, text=True, timeout=120, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            marker = os.path.join(mine, ".ambient-codex")
            self.assertTrue(os.path.exists(marker), "state root was not claimed")
            if os.name != "nt":  # Windows chmod cannot express 0o600
                self.assertEqual(os.stat(marker).st_mode & 0o777, 0o600)

    def test_a_root_we_already_claimed_is_reused(self):
        with tempfile.TemporaryDirectory() as home:
            mine = os.path.join(home, "mine")
            env = {**sandbox_env(home), "AMBIENT_CODEX_HOME": mine}
            for _ in range(2):
                proc = subprocess.run(
                    [sys.executable, str(CLI), "control", "mode", "on"],
                    env=env, capture_output=True, text=True, timeout=120, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_default_root_never_requires_a_marker(self):
        """An existing 1.6.x user's ~/.config/ambient-codex has no marker yet."""
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".config", "ambient-codex")
            os.makedirs(root)
            with open(os.path.join(root, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=on\n")
            proc = subprocess.run(
                [sys.executable, str(CLI), "control", "mode", "off"],
                env=sandbox_env(home), capture_output=True, text=True,
                timeout=120, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("AMBIENT_DELEGATE=off",
                          Path(root, "env").read_text(encoding="utf-8"))


class TestNoWritesEscapeTheCodexRoot(unittest.TestCase):
    def test_mode_takeover_never_writes_the_shared_env(self):
        """The exact reported bug: takeover in Codex must not reach the other install."""
        with tempfile.TemporaryDirectory() as home:
            shared_dir = os.path.join(home, ".config", "ambient")
            os.makedirs(shared_dir, mode=0o700)
            shared_env = os.path.join(shared_dir, "env")
            with open(shared_env, "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=off\nAMBIENT_MODEL=frontier/expensive\n")
            before = Path(shared_env).read_text(encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(CLI), "control", "mode", "takeover"],
                env=sandbox_env(home), capture_output=True, text=True,
                timeout=120, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            self.assertEqual(
                Path(shared_env).read_text(encoding="utf-8"), before,
                "Codex mutated the other Ambient install's env file")
            codex_env = Path(home) / ".config" / "ambient-codex" / "env"
            self.assertIn("AMBIENT_DELEGATE=takeover",
                          codex_env.read_text(encoding="utf-8"))

    def test_shared_dir_is_never_created_when_absent(self):
        with tempfile.TemporaryDirectory() as home:
            proc = subprocess.run(
                [sys.executable, str(CLI), "control", "mode", "on"],
                env=sandbox_env(home), capture_output=True, text=True,
                timeout=120, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(
                os.path.exists(os.path.join(home, ".config", "ambient")),
                "Codex created the other install's config dir")
            self.assertTrue(
                os.path.exists(os.path.join(home, ".config", "ambient-codex", "env")))


class TestGitHookOwnership(unittest.TestCase):
    def test_codex_never_claims_the_other_installs_hook_marker(self):
        """Uninstalling hooks from Codex must not delete the Claude install's hook."""
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            self.assertEqual(cli.AMBIENT_HOOK_MARKER, "# ambient-codex audit hook v1")
            self.assertEqual(cli.LEGACY_AMBIENT_HOOK_MARKERS, ())
            source = CLI.read_text(encoding="utf-8")
            self.assertNotIn('"# ambient-code audit hook v1"', source)


class TestPathLauncherName(unittest.TestCase):
    def test_launcher_is_not_named_ambient(self):
        """`~/.local/bin/ambient` belongs to whichever install claimed it first.

        The Claude plugin symlinks that exact name, so Codex must install its own
        launcher as `ambient-codex` rather than race for the shared one.
        """
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            self.assertEqual(cli.LAUNCHER_NAME, "ambient-codex")

    def test_link_writes_the_codex_launcher(self):
        with tempfile.TemporaryDirectory() as home:
            dest = os.path.join(home, "bin")
            proc = subprocess.run(
                [sys.executable, str(CLI), "link", "--dir", dest],
                env=sandbox_env(home), capture_output=True, text=True,
                timeout=120, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Windows cannot symlink without privileges, so `link` writes a .cmd shim.
            suffix = ".cmd" if os.name == "nt" else ""
            self.assertTrue(os.path.lexists(os.path.join(dest, "ambient-codex" + suffix)))
            self.assertFalse(os.path.lexists(os.path.join(dest, "ambient" + suffix)),
                             "Codex claimed the shared `ambient` name on PATH")

    @unittest.skipIf(os.name == "nt", "POSIX launcher integration")
    def test_terminal_launcher_survives_plugin_cache_rotation(self):
        """A copied launcher must resolve the active cache after its source dies."""
        with tempfile.TemporaryDirectory() as home:
            old_root = Path(home) / "cache" / "ambient-codex" / "old"
            active_root = Path(home) / "cache" / "ambient-codex" / "active"
            for root in (old_root, active_root):
                (root / "bin").mkdir(parents=True)
                shutil.copy2(CLI, root / "bin" / "ambient")
                shutil.copytree(ROOT / "ambient_codex", root / "ambient_codex")
                if STABLE_LAUNCHER.exists():
                    (root / "scripts").mkdir()
                    shutil.copy2(STABLE_LAUNCHER,
                                 root / "scripts" / STABLE_LAUNCHER.name)

            launcher_dir = Path(home) / "launcher-bin"
            launcher_dir.mkdir()
            install = subprocess.run(
                [sys.executable, str(old_root / "bin" / "ambient"), "link",
                 "--dir", str(launcher_dir)],
                env=sandbox_env(home), capture_output=True, text=True,
                timeout=120, check=False)
            self.assertEqual(install.returncode, 0, install.stderr)
            launcher = launcher_dir / "ambient-codex"
            self.assertFalse(launcher.is_symlink())

            fake_bin = Path(home) / "fake-bin"
            fake_bin.mkdir()
            codex = fake_bin / "codex"
            codex.write_text(
                "#!/bin/sh\nprintf '%s\\n' '"
                + json.dumps({"transport": {"cwd": str(active_root / ".")}})
                + "'\n",
                encoding="utf-8")
            os.chmod(codex, 0o755)
            shutil.rmtree(old_root)
            env = sandbox_env(home, PATH=str(fake_bin) + os.pathsep
                              + os.environ.get("PATH", ""))
            proc = subprocess.run([str(launcher), "--version"], env=env,
                                  capture_output=True, text=True, timeout=120,
                                  check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("ambient", proc.stdout.lower())


class TestOpencodeProviderIsNamespaced(unittest.TestCase):
    """opencode keeps ONE global config, shared with every other tool on the box.

    Both Ambient installs used to write `provider["ambient"]` into
    ~/.config/opencode/opencode.json. Whichever ran `agent` first pinned the
    baseURL (`ambient trust-url` can point it at a private gateway), and the other
    install then sent ITS key to that endpoint, because the existing-provider branch
    only unions a model in and never rewrites `options`.
    """

    def test_provider_key_is_install_scoped(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            self.assertEqual(cli.OPENCODE_PROVIDER, "ambient-codex")
            self.assertNotEqual(cli.OPENCODE_PROVIDER, "ambient")

    def test_the_other_installs_provider_entry_is_left_alone(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            cfg_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "opencode.json")
            foreign = {
                "provider": {
                    "ambient": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": "https://someone-elses-gateway/v1",
                                    "apiKey": "{env:AMBIENT_API_KEY}"},
                        "models": {"old/model": {"name": "old/model"}},
                    }
                }
            }
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(foreign, fh)
            cli.OPENCODE_CONFIG_PATH = cfg_path

            cli.ensure_opencode_config("https://api.ambient.xyz", "new/model")

            with open(cfg_path, encoding="utf-8") as fh:
                after = json.load(fh)
            self.assertEqual(after["provider"]["ambient"], foreign["provider"]["ambient"],
                             "Codex mutated the other install's opencode provider")
            ours = after["provider"]["ambient-codex"]
            self.assertEqual(ours["options"]["baseURL"], "https://api.ambient.xyz/v1")
            self.assertIn("new/model", ours["models"])

    def test_no_literal_key_is_written_to_the_opencode_config(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            cfg_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "opencode.json")
            cli.OPENCODE_CONFIG_PATH = cfg_path
            cli.ensure_opencode_config("https://api.ambient.xyz", "m/1")
            body = Path(cfg_path).read_text(encoding="utf-8")
            self.assertIn("{env:AMBIENT_CODEX_API_KEY}", body)
            self.assertNotIn("{env:AMBIENT_API_KEY}", body)
            self.assertNotIn("sk-", body)

    def test_non_object_provider_collection_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            cfg_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "opencode.json")
            original = '{"provider": ["user-owned"]}\n'
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(original)
            cli.OPENCODE_CONFIG_PATH = cfg_path

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cli.ensure_opencode_config("https://api.ambient.xyz", "m/1")

            self.assertEqual(Path(cfg_path).read_text(encoding="utf-8"), original)
            self.assertIn("provider", err.getvalue())
            self.assertIn("leaving it alone", err.getvalue())

    def test_malformed_native_provider_is_repaired_without_touching_foreign(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            cfg_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "opencode.json")
            foreign = {"options": {"apiKey": "user-secret-placeholder"}}
            before = {
                "provider": {
                    "openai": foreign,
                    "ambient-codex": "malformed-old-entry",
                }
            }
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(before, fh)
            cli.OPENCODE_CONFIG_PATH = cfg_path

            cli.ensure_opencode_config("https://api.ambient.xyz", "m/1")

            after = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            self.assertEqual(after["provider"]["openai"], foreign)
            ours = after["provider"]["ambient-codex"]
            self.assertIsInstance(ours, dict)
            self.assertEqual(ours["models"], {"m/1": {"name": "m/1"}})

    def test_malformed_native_models_map_is_repaired(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            cfg_dir = os.path.join(home, ".config", "opencode")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "opencode.json")
            before = {
                "provider": {
                    "ambient-codex": {
                        "options": {"baseURL": "https://old.invalid/v1"},
                        "models": ["malformed"],
                    }
                }
            }
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(before, fh)
            cli.OPENCODE_CONFIG_PATH = cfg_path

            cli.ensure_opencode_config("https://api.ambient.xyz", "m/1")

            after = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            ours = after["provider"]["ambient-codex"]
            self.assertEqual(ours["models"], {"m/1": {"name": "m/1"}})
            self.assertEqual(
                ours["options"]["apiKey"],
                "{env:AMBIENT_CODEX_API_KEY}",
            )


class TestGuidanceNeverNamesTheSharedLauncher(unittest.TestCase):
    def test_no_printed_command_starts_with_a_bare_ambient(self):
        """Copy-pasteable guidance must name THIS install's launcher.

        `ambient use ...`, `ambient mode on`, and `ambient config set ...` all mutate
        state, and a bare `ambient` on PATH is the other install. `ambient audit`
        would even spend its credits.
        """
        subcommands = (
            "config", "use", "mode", "control", "curate", "trust-url", "setup",
            "models", "audit", "usage", "doctor", "link", "cache", "chat", "ask",
            "code", "build", "map", "agent",
        )
        pattern = re.compile(
            r"(?<![-\w./])ambient (?=(?:" + "|".join(subcommands) + r")\b)")
        skip = ("audit hook v1", "Installed by:", "AMBIENT_CODE_MODEL",
                "ambient use auto", "ambient use)")
        offenders = []
        for lineno, line in enumerate(CLI.read_text(encoding="utf-8").split("\n"), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or "LAUNCHER_NAME" in line:
                continue
            # Do NOT skip quote-less lines: a multi-line print triple-quoted block
            # continuation carries no quote of its own, and that is exactly how the
            # post-setup welcome panel's bare `ambient` commands slipped past before.
            if any(tok in line for tok in skip):
                continue
            if pattern.search(line):
                offenders.append(f"{lineno}: {stripped[:70]}")
        self.assertEqual(offenders, [], "bare `ambient <subcommand>` in user-facing text")

    def test_the_git_hook_never_invokes_a_bare_ambient(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            for name in ("pre-commit", "pre-push"):
                body = cli._render_hook(name)
                self.assertIn("command -v ambient-codex", body)
                self.assertNotIn("command -v ambient >", body)
                self.assertIn('"$AMBIENT_BIN" audit', body)


class TestNoCrossInstallKeyAccess(unittest.TestCase):
    """Each install holds its own key. There is no import, no probe, no peeking.

    1.6.0 briefly offered a TTY-gated import that read the other install's keychain
    item. That is still sharing a secret across installs, so the whole path is gone.
    """

    FORBIDDEN_SYMBOLS = (
        "keychain_has", "find_foreign_key_source", "read_foreign_key",
        "offer_foreign_key_import", "maybe_import_foreign_key",
        "LEGACY_KEYCHAIN_SERVICE", "LEGACY_SHARED_DIR",
    )

    def test_no_cross_install_symbol_survives(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            for name in self.FORBIDDEN_SYMBOLS:
                self.assertFalse(hasattr(cli, name),
                                 f"{name} still exists; it can reach another install")

    def test_keychain_read_cannot_be_pointed_at_another_item(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            import inspect
            params = list(inspect.signature(cli.keychain_read).parameters)
            self.assertEqual(params, [], "keychain_read takes a service argument")

    def test_source_never_uses_ambient_xyz_as_a_keychain_service(self):
        """`ambient.xyz` is the API domain AND the other install's keychain service.

        The domain is legitimate; naming it near secret-store code is not.
        """
        offenders = []
        for path in (CLI, CREDENTIALS):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                low = line.lower()
                if "ambient.xyz" not in low:
                    continue
                if any(t in low for t in ("keychain", "secret-tool", "security",
                                          "find-generic-password", "service")):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()[:70]}")
        self.assertEqual(offenders, [], "ambient.xyz used as a secret-store service")

    def test_keychain_service_is_only_ever_this_installs_item(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            self.assertEqual(cli.KEYCHAIN_SERVICE, "ambient-codex")
        source = CREDENTIALS.read_text(encoding="utf-8")
        # The internal adapter accepts an explicit service; the public facade
        # exposes no service argument and always supplies KEYCHAIN_SERVICE.
        self.assertNotIn('"-s", "ambient.xyz"', source)
        self.assertIn('"-s", service', source)

    ALLOWED_FOREIGN_DIR_LINES = (
        'os.path.expanduser("~/.config/ambient"),',
        'os.path.expanduser("~/.claude"),',
    )

    def test_the_other_installs_config_dir_is_only_ever_refused(self):
        """The only place we may name it is the guard that refuses to use it."""
        offenders = []
        for lineno, line in enumerate(CLI.read_text(encoding="utf-8").split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue  # prose may explain the boundary
            if line.strip() in self.ALLOWED_FOREIGN_DIR_LINES:
                continue
            if ('".config/ambient/' in line or '"~/.config/ambient"' in line
                    or "'~/.config/ambient'" in line):
                offenders.append(f"{lineno}: {line.strip()[:70]}")
        self.assertEqual(offenders, [])

    def test_the_foreign_trees_are_only_used_to_refuse(self):
        source = CLI.read_text(encoding="utf-8")
        uses = [ln.strip() for ln in source.split("\n")
                if "FOREIGN_STATE_DIRS" in ln and not ln.lstrip().startswith("#")]
        # Definition + two refusal-only adapters: foreign_root selection and
        # state-root validation. No read/write path may consume this tuple.
        self.assertEqual(len(uses), 3, uses)

    def test_setup_never_offers_an_import(self):
        source = CLI.read_text(encoding="utf-8")
        self.assertNotIn("Import that key", source)
        self.assertNotIn("offers to import", source)
        self.assertNotIn("Found an existing Ambient API key", source)


class TestRunsWithTheOtherInstallLockedOut(unittest.TestCase):
    """The strongest statement of isolation: make the other install unreadable.

    If `~/.config/ambient` and `~/.claude` are mode 000 and every command still
    succeeds, nothing in this plugin reads them. Root bypasses file permissions, so
    the test is meaningless there.
    """

    COMMANDS = (
        ["control"],
        ["control", "mode", "on"],
        ["control", "mode", "takeover"],
        ["control", "mode", "off"],
        ["control", "setting", "streaming", "off"],
        ["curate", "reset"],
        ["control", "key", "status"],
        ["control", "key", "remove"],
    )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores mode 000")
    def test_every_command_works_with_the_other_install_unreadable(self):
        with tempfile.TemporaryDirectory() as home:
            other = os.path.join(home, ".config", "ambient")
            claude = os.path.join(home, ".claude")
            os.makedirs(other)
            os.makedirs(claude)
            secret = os.path.join(other, "env")
            with open(secret, "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_API_KEY=sk-other-install-secret\nAMBIENT_DELEGATE=takeover\n")
            os.chmod(other, 0o000)
            os.chmod(claude, 0o000)
            try:
                with open(secret, encoding="utf-8"):  # sanity: really locked
                    self.fail("the other install's env is still readable")
            except PermissionError:
                pass

            try:
                for argv in self.COMMANDS:
                    with self.subTest(cmd=" ".join(argv)):
                        proc = subprocess.run(
                            [sys.executable, str(CLI), *argv], env=sandbox_env(home),
                            capture_output=True, text=True, timeout=120, check=False)
                        self.assertNotIn("Permission denied", proc.stderr)
                        self.assertIn(proc.returncode, (0, 1, 3),
                                      f"{argv} failed: {proc.stderr[:200]}")
                # this install's own mode must NOT have inherited `takeover`
                codex_env = Path(home) / ".config" / "ambient-codex" / "env"
                self.assertNotIn("AMBIENT_DELEGATE=takeover",
                                 codex_env.read_text(encoding="utf-8"))
            finally:
                os.chmod(other, 0o700)
                os.chmod(claude, 0o700)


class TestKeyEnvIsNamespaced(unittest.TestCase):
    def test_namespaced_var_supplies_the_key(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            with mock.patch.dict(os.environ, {"AMBIENT_CODEX_API_KEY": "mine",
                                              "AMBIENT_API_KEY": "shared"}):
                key, backend = cli.resolve_key_and_backend({})
            self.assertEqual(key, "mine")
            self.assertEqual(backend, "env")

    def test_the_shared_var_is_ignored_entirely(self):
        """`AMBIENT_API_KEY` is read by EVERY Ambient install; honouring it shares a key."""
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            env = {k: v for k, v in os.environ.items() if k != "AMBIENT_CODEX_API_KEY"}
            env["AMBIENT_API_KEY"] = "the-other-installs-key"
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(cli, "keychain_read", return_value=None):
                key, backend = cli.resolve_key_and_backend({})
            self.assertIsNone(key, "adopted the shared AMBIENT_API_KEY")
            self.assertIsNone(backend)

    def test_doctor_reports_the_shared_var_as_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            with mock.patch.dict(os.environ, {"AMBIENT_API_KEY": "x"}):
                self.assertTrue(cli.shared_key_env_is_set())
            env = {k: v for k, v in os.environ.items() if k != "AMBIENT_API_KEY"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertFalse(cli.shared_key_env_is_set())

    def test_key_removal_only_targets_this_installs_item(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            with mock.patch.object(cli, "secret_backend", return_value="keychain"), \
                 mock.patch.object(cli.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
                cli.keychain_delete()
            argv = run.call_args[0][0]
            self.assertIn("ambient-codex", argv)
            self.assertNotIn("ambient.xyz", argv)


class TestStressHarnessIsolation(unittest.TestCase):
    def test_live_harnesses_use_codex_key_namespace_only(self):
        for path in (STRESS_HARNESS, MODEL_MATRIX):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("ambient-codex", source)
                self.assertIn("AMBIENT_CODEX_API_KEY", source)
                self.assertNotIn("find-generic-password -s ambient.xyz", source)
                self.assertNotIn(".config/ambient/env", source)
                self.assertNotIn("env AMBIENT_API_KEY", source)
                self.assertNotIn("with_key() { AMBIENT_API_KEY", source)
                self.assertNotIn('AMBIENT_API_KEY="$KEY"', source)


class TestHookScriptReadsIsolatedEnv(unittest.TestCase):
    def test_session_start_reads_the_codex_root_only(self):
        script = (ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
        self.assertIn('${AMBIENT_CODEX_HOME:-$HOME/.config/ambient-codex}', script)
        self.assertNotIn('conf="$HOME/.config/ambient/env"', script)

    def test_session_start_validates_a_relocated_state_root(self):
        """The hook must not read a root the CLI would refuse."""
        script = (ROOT / "hooks" / "session-start.sh").read_text(encoding="utf-8")
        self.assertIn('$HOME/.config/ambient"', script)   # the foreign tree it checks
        self.assertIn('$HOME/.claude"', script)
        self.assertIn("exit 0", script)

    @unittest.skipIf(os.name == "nt", "POSIX sh hook")
    def test_session_start_stays_silent_for_a_hostile_state_root(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = os.path.join(home, ".config", "ambient")
            os.makedirs(foreign)
            with open(os.path.join(foreign, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=takeover\n")
            for override in (foreign, os.path.join(foreign, "sub"),
                             os.path.join(home, ".claude")):
                with self.subTest(override=override):
                    proc = subprocess.run(
                        ["sh", str(ROOT / "hooks" / "session-start.sh")],
                        env={**sandbox_env(home), "PLUGIN_ROOT": str(ROOT),
                             "AMBIENT_CODEX_HOME": override},
                        capture_output=True, text=True, timeout=30, check=False)
                    self.assertNotIn("TAKEOVER", proc.stdout)

    @unittest.skipIf(os.name == "nt", "SessionStart hook is POSIX sh")
    def test_session_start_ignores_the_other_installs_takeover_flag(self):
        with tempfile.TemporaryDirectory() as home:
            shared_dir = os.path.join(home, ".config", "ambient")
            os.makedirs(shared_dir, mode=0o700)
            with open(os.path.join(shared_dir, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=takeover\n")
            proc = subprocess.run(
                ["sh", str(ROOT / "hooks" / "session-start.sh")],
                env=sandbox_env(home, PLUGIN_ROOT=str(ROOT)),
                capture_output=True, text=True, timeout=30, check=False)
            self.assertNotIn("TAKEOVER", proc.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class TestMcpHonoursTheSameStateRootGuard(unittest.TestCase):
    """The MCP server reads the mode file directly, so it needs the CLI's guard.

    Without it, `AMBIENT_CODEX_HOME=~/.config/ambient` made the server surface the
    OTHER install's delegate mode in its `initialize` instructions.
    """

    def _mcp(self):
        spec = importlib.util.spec_from_file_location(
            "ambient_mcp_guard", ROOT / "mcp" / "ambient_mcp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_foreign_trees_match_the_cli(self):
        with tempfile.TemporaryDirectory() as home:
            cli = load_cli(home)
            mcp = self._mcp()
        cli_trees = {os.path.basename(p.rstrip("/")) for p in cli.FOREIGN_STATE_DIRS}
        mcp_trees = {os.path.basename(p.rstrip("/")) for p in mcp.FOREIGN_STATE_DIRS}
        self.assertEqual(cli_trees, mcp_trees, "guards drifted apart")

    def test_state_root_is_none_for_a_foreign_override(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as home:
            for sub in (os.path.join(home, ".config", "ambient"),
                        os.path.join(home, ".config", "ambient", "cache"),
                        os.path.join(home, ".claude")):
                os.makedirs(sub, exist_ok=True)
                with self.subTest(sub=sub), \
                     mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home,
                                                  "AMBIENT_CODEX_HOME": sub}):
                    self.assertIsNone(mcp.state_root())

    def test_mode_is_off_when_the_override_is_foreign(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as home:
            foreign = os.path.join(home, ".config", "ambient")
            os.makedirs(foreign)
            with open(os.path.join(foreign, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=takeover\n")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home,
                                              "AMBIENT_CODEX_HOME": foreign}):
                self.assertEqual(mcp.current_mode(), "off")
                self.assertNotIn("TAKEOVER", mcp.session_instructions())

    def test_a_legitimate_override_is_still_read(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as home:
            mine = os.path.join(home, "mine")
            os.makedirs(mine)
            with open(os.path.join(mine, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_DELEGATE=takeover\n")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home,
                                              "AMBIENT_CODEX_HOME": mine}):
                self.assertEqual(mcp.current_mode(), "takeover")


class TestMcpNeverDrivesAStalePluginRoot(unittest.TestCase):
    """`PLUGIN_ROOT` survives a plugin update, so a 1.7.x server could drive an old CLI."""

    def _mcp(self):
        spec = importlib.util.spec_from_file_location(
            "ambient_mcp_root", ROOT / "mcp" / "ambient_mcp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _fake_plugin(self, tmp, name, version):
        root = Path(tmp) / f"{name}-{version}"
        (root / "bin").mkdir(parents=True)
        (root / ".codex-plugin").mkdir()
        (root / "bin" / "ambient").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8")
        return root

    def test_a_version_mismatched_plugin_root_is_ignored(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as tmp:
            stale = self._fake_plugin(tmp, mcp.SERVER_NAME, "0.0.1")
            with mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(stale)}):
                self.assertEqual(mcp.plugin_root(), Path(ROOT).resolve())

    def test_a_foreign_named_plugin_root_is_ignored(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as tmp:
            other = self._fake_plugin(tmp, "ambient-code", mcp.SERVER_VERSION)
            with mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(other)}):
                self.assertEqual(mcp.plugin_root(), Path(ROOT).resolve())

    def test_a_matching_plugin_root_is_honoured_including_the_cachebuster(self):
        mcp = self._mcp()
        with tempfile.TemporaryDirectory() as tmp:
            good = self._fake_plugin(tmp, mcp.SERVER_NAME,
                                     f"{mcp.SERVER_VERSION}+codex.20260709")
            with mock.patch.dict(os.environ, {"PLUGIN_ROOT": str(good)}):
                self.assertEqual(mcp.plugin_root(), good.resolve())


class TestTraceFileIsNamespaced(unittest.TestCase):
    def test_the_trace_env_var_is_install_scoped(self):
        source = (ROOT / "mcp" / "ambient_mcp.py").read_text(encoding="utf-8")
        self.assertIn("AMBIENT_CODEX_MCP_TRACE_FILE", source)
        self.assertNotIn('"AMBIENT_MCP_TRACE_FILE"', source)


class TestGuidanceNeverTellsUserToExportTheSharedKey(unittest.TestCase):
    def test_no_user_facing_text_suggests_exporting_AMBIENT_API_KEY(self):
        """Guiding a user to `export AMBIENT_API_KEY` would key BOTH installs at once."""
        source = CLI.read_text(encoding="utf-8")
        offenders = []
        for lineno, line in enumerate(source.split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue
            if '"' not in line and "'" not in line:
                continue  # code, not printed copy
            if "export AMBIENT_API_KEY" in line:
                offenders.append(f"{lineno}: {line.strip()[:60]}")
            if "the AMBIENT_API_KEY environment variable" in line:
                offenders.append(f"{lineno}: {line.strip()[:60]}")
        self.assertEqual(offenders, [])


class TestUninstallTouchesOnlyThisInstall(unittest.TestCase):
    """`ambient-codex uninstall` must remove ONLY this install's key, launcher, and
    (with --purge) its own state — never another Ambient install's anything."""

    def _run(self, home, *extra, codex_home=None):
        env = {**sandbox_env(home), **({"AMBIENT_CODEX_HOME": codex_home} if codex_home else {})}
        return subprocess.run(
            [sys.executable, str(CLI), "uninstall", "--yes", *extra],
            env=env, capture_output=True, text=True, timeout=120, check=False)

    def _seed_foreign(self, home):
        foreign = os.path.join(home, ".config", "ambient")
        os.makedirs(foreign, exist_ok=True)
        env = os.path.join(foreign, "env")
        with open(env, "w", encoding="utf-8") as fh:
            fh.write("AMBIENT_API_KEY=sk-not-ours\nAMBIENT_DELEGATE=takeover\n")
        return env, sha1_file(env)

    def test_default_scrubs_key_keeps_state_removes_launcher(self):
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".config", "ambient-codex")
            os.makedirs(root)
            with open(os.path.join(root, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_API_KEY=sk-x\nAMBIENT_MODEL=z-ai/glm-5.2\n")
            binp = os.path.join(home, ".local", "bin")
            os.makedirs(binp)
            subprocess.run([sys.executable, str(CLI), "link", "--dir", binp],
                           env=sandbox_env(home), capture_output=True, timeout=120)
            foreign_env, foreign_hash = self._seed_foreign(home)

            proc = self._run(home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = Path(root, "env").read_text(encoding="utf-8")
            self.assertNotIn("AMBIENT_API_KEY", body)      # key scrubbed
            self.assertIn("AMBIENT_MODEL", body)           # settings kept
            self.assertFalse(os.path.lexists(os.path.join(binp, "ambient-codex")))
            self.assertEqual(
                sha1_file(foreign_env),
                foreign_hash, "uninstall touched the other install's env")

    def test_purge_deletes_only_the_codex_state_dir(self):
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".config", "ambient-codex")
            os.makedirs(os.path.join(root, "cache"))
            with open(os.path.join(root, "env"), "w", encoding="utf-8") as fh:
                fh.write("AMBIENT_API_KEY=sk-x\n")
            foreign_env, foreign_hash = self._seed_foreign(home)

            proc = self._run(home, "--purge")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(os.path.isdir(root), "codex state dir not deleted")
            self.assertTrue(os.path.isdir(os.path.dirname(foreign_env)),
                            "the other install's dir was deleted")
            self.assertEqual(
                sha1_file(foreign_env),
                foreign_hash)

    def test_purge_refuses_when_state_root_is_the_other_install(self):
        with tempfile.TemporaryDirectory() as home:
            foreign_env, foreign_hash = self._seed_foreign(home)
            foreign_dir = os.path.dirname(foreign_env)
            proc = self._run(home, "--purge", codex_home=foreign_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("another Ambient install", proc.stderr + proc.stdout)
            self.assertTrue(os.path.isdir(foreign_dir))
            self.assertEqual(
                sha1_file(foreign_env),
                foreign_hash)

    def test_purge_reports_failure_instead_of_lying(self):
        """A partial rmtree must not print 'Deleted all state'."""
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            self.skipTest("POSIX non-root permission bits")
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".config", "ambient-codex")
            locked = os.path.join(root, "locked")
            os.makedirs(locked)
            with open(os.path.join(locked, "f"), "w", encoding="utf-8") as fh:
                fh.write("stuck")
            os.chmod(locked, 0o000)
            try:
                proc = self._run(home, "--purge")
            finally:
                os.chmod(locked, 0o755)
            self.assertNotIn("Deleted all state", proc.stdout)
            self.assertIn("Could not fully delete", proc.stderr)

    def test_dir_flag_unlinks_a_custom_launcher(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".config", "ambient-codex"))
            custom = os.path.join(home, "custom-bin")
            os.makedirs(custom)
            subprocess.run([sys.executable, str(CLI), "link", "--dir", custom],
                           env=sandbox_env(home), capture_output=True, timeout=120)
            suffix = ".cmd" if os.name == "nt" else ""
            self.assertTrue(os.path.lexists(os.path.join(custom, "ambient-codex" + suffix)))
            proc = self._run(home, "--dir", custom)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(os.path.lexists(os.path.join(custom, "ambient-codex" + suffix)))

    def test_refuses_when_state_root_is_foreign_even_without_purge(self):
        with tempfile.TemporaryDirectory() as home:
            foreign_env, foreign_hash = self._seed_foreign(home)
            proc = self._run(home, codex_home=os.path.dirname(foreign_env))
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(
                sha1_file(foreign_env),
                foreign_hash, "foreign env was scrubbed before the guard fired")

    def test_it_prints_the_codex_plugin_remove_command(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".config", "ambient-codex"))
            proc = self._run(home)
            self.assertIn("codex plugin remove ambient-codex", proc.stdout)
