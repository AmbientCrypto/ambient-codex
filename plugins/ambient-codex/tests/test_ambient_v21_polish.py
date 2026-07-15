"""Polish + hardening (post-review hardening).

Pins the two NEW behaviors this batch introduced:

1. `ambient agent` preserves the secrets-tripwire warning at launch without
   introducing money language into the public terminal surface.

2. hooks/session-start.sh launcher migration scoping: the hook replaces only an
   owned legacy symlink with the stable launcher after plugin cache rotation. It
   must NEVER clobber a real (non-symlink) file or a foreign symlink, and it
   must not create anything when nothing is there. No
   PATH-wide healing: a foreign tool coincidentally named `ambient` elsewhere
   is out of scope by design.

No network, no live API, no writes outside tempdirs (the hook runs against a
throwaway $HOME).
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "ambient")
HOOK = os.path.join(ROOT, "hooks", "session-start.sh")
STABLE_LAUNCHER = os.path.join(ROOT, "scripts", "ambient-codex-launcher.py")

KEY = "sk-test-key-abcdef1234567890"


def load_module():
    loader = importlib.machinery.SourceFileLoader("ambient_cli_v21", BIN)
    spec = importlib.util.spec_from_loader("ambient_cli_v21", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


amb = load_module()


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {}
    missing = object()
    for k, v in attrs.items():
        old[k] = getattr(obj, k, missing)
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is missing:
                delattr(obj, k)
            else:
                setattr(obj, k, v)


class _Handoff(Exception):
    """Raised by the fake execvpe so the test regains control."""


class TestAgentLaunchWarnings(unittest.TestCase):
    """The agent handoff retains safety guidance without money language."""

    def _run_agent(self):
        record = {}
        err = io.StringIO()

        def fake_execvpe(prog, argv, env):
            # Snapshot stderr AT the handoff — anything printed later would
            # never be seen (execvpe replaces the process image).
            record["prog"] = prog
            record["argv"] = argv
            record["stderr_at_handoff"] = err.getvalue()
            raise _Handoff()

        def fake_run(cmd, **kw):
            # Windows path: cmd_agent runs opencode as a child (subprocess.run)
            # instead of execvpe. Record the same way (basename, so a which()-
            # resolved absolute path still reads as "opencode").
            record["prog"] = os.path.basename(cmd[0])
            record["argv"] = cmd
            record["stderr_at_handoff"] = err.getvalue()
            raise _Handoff()

        args = argparse.Namespace(model="deepseek-ai/DeepSeek-V3",
                                  agent_args=[])
        with tempfile.TemporaryDirectory() as tmp:
            with patched(amb, OPENCODE_CONFIG_PATH=os.path.join(
                    tmp, "opencode.json")), \
                 patched(amb.shutil, which=lambda name:
                         "/usr/bin/opencode" if name == "opencode" else None), \
                 patched(amb.os, execvpe=fake_execvpe), \
                 patched(amb.subprocess, run=fake_run), \
                 contextlib.redirect_stderr(err):
                with self.assertRaises(_Handoff):
                    amb.cmd_agent(args, KEY, "https://api.example.invalid",
                                  {})
        return record, err.getvalue()

    def test_secrets_warning_printed_before_handoff(self):
        record, _ = self._run_agent()
        self.assertEqual(record["prog"], "opencode")
        self.assertIn("secrets tripwire", record["stderr_at_handoff"])

    def test_handoff_output_has_no_money_language(self):
        record, _ = self._run_agent()
        pre = record["stderr_at_handoff"]
        self.assertNotRegex(pre.lower(), r"\b(?:cost|price|spend|billed|dollar|cent)\b")

    def test_agent_uses_opencode_pure_mode_by_default(self):
        record, _ = self._run_agent()
        self.assertIn("--pure", record["argv"])
        self.assertEqual(record["argv"].count("--pure"), 1)


@unittest.skipIf(os.name == "nt",
                 "SessionStart self-heal is POSIX-sh + symlinks; Windows "
                 "installs use the ambient.cmd shim path instead")
class TestSessionStartSelfHeal(unittest.TestCase):
    """The hook migrates ONLY an owned legacy launcher, only when safe.

    The launcher is deliberately NOT named `ambient`: another Ambient install owns
    that name on PATH, and the two must be able to coexist.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # realpath: on macOS tempdirs may sit behind symlinked parents, and
        # the CLI's `link` resolves its own path — compare apples to apples.
        base = os.path.realpath(self._tmp.name)
        self.home = os.path.join(base, "home")
        self.root = os.path.join(base, "plugin-cache", "ambient-codex", "2.0.0")
        os.makedirs(os.path.join(self.home, ".local", "bin"))
        os.makedirs(os.path.join(self.root, "bin"))
        shutil.copytree(os.path.join(ROOT, "ambient_codex"),
                        os.path.join(self.root, "ambient_codex"))
        os.makedirs(os.path.join(self.root, "scripts"))
        shutil.copyfile(STABLE_LAUNCHER,
                        os.path.join(self.root, "scripts", "ambient-codex-launcher.py"))
        self.active = os.path.join(self.root, "bin", "ambient")
        shutil.copyfile(BIN, self.active)
        os.chmod(self.active, os.stat(self.active).st_mode
                 | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.link = os.path.join(self.home, ".local", "bin", "ambient-codex")

    def _run_hook(self, *, plugin_root=True, claude_plugin_root=None):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("AMBIENT_")}
        env["HOME"] = self.home
        if plugin_root:
            env["PLUGIN_ROOT"] = self.root
        else:
            env.pop("PLUGIN_ROOT", None)
        if claude_plugin_root is not None:
            env["CLAUDE_PLUGIN_ROOT"] = claude_plugin_root
        else:
            env.pop("CLAUDE_PLUGIN_ROOT", None)
        return subprocess.run(["sh", HOOK], env=env, capture_output=True,
                              text=True, timeout=60)

    def _assert_silent_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_dangling_symlink_is_migrated_to_the_stable_launcher(self):
        # The post-GC scenario: an owned versioned-cache symlink must become a
        # copied launcher rather than another cache symlink.
        gone = os.path.join(os.path.dirname(self.root), "1.9.0", "bin",
                            "ambient")
        os.symlink(gone, self.link)
        self.assertTrue(os.path.islink(self.link))
        self.assertFalse(os.path.exists(self.link))  # dangling
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertFalse(os.path.islink(self.link))
        with open(self.link, encoding="utf-8") as fh:
            self.assertIn("ambient-codex stable launcher v1", fh.read())

    def test_stale_existing_symlink_is_migrated_to_the_stable_launcher(self):
        old_root = os.path.join(os.path.dirname(self.root), "1.9.0", "bin")
        os.makedirs(old_root)
        old = os.path.join(old_root, "ambient")
        shutil.copyfile(BIN, old)
        os.chmod(old, 0o755)
        os.symlink(old, self.link)
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertFalse(os.path.islink(self.link))
        with open(self.link, encoding="utf-8") as fh:
            self.assertIn("ambient-codex stable launcher v1", fh.read())

    def test_healing_is_idempotent(self):
        shutil.copyfile(STABLE_LAUNCHER, self.link)
        os.chmod(self.link, 0o755)
        before = os.lstat(self.link).st_ino
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertEqual(os.lstat(self.link).st_ino, before)  # untouched

    def test_real_file_is_never_clobbered(self):
        payload = "#!/bin/sh\necho this is the user's own ambient\n"
        with open(self.link, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.chmod(self.link, 0o755)
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertFalse(os.path.islink(self.link))
        with open(self.link, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), payload)

    def test_foreign_symlink_is_never_repointed(self):
        # An EXISTING target that is clearly not an ambient-plugin launcher
        # (basename != ambient) must be left exactly as the user set it.
        foreign_dir = os.path.join(self.home, "tools")
        os.makedirs(foreign_dir)
        foreign = os.path.join(foreign_dir, "ambient-music-player")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho lo-fi beats\n")
        os.chmod(foreign, 0o755)
        os.symlink(foreign, self.link)
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.readlink(self.link), foreign)

    def test_foreign_symlink_named_ambient_is_never_repointed(self):
        # The exact H2 gap the gap found was: a DIFFERENT tool literally
        # named `ambient` (target BASENAME is `ambient`, but its path has no
        # ambient-codex component). The old basename-only guard would clobber it.
        foreign_dir = os.path.join(self.home, "othertool", "bin")
        os.makedirs(foreign_dir)
        foreign = os.path.join(foreign_dir, "ambient")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho a totally different ambient\n")
        os.chmod(foreign, 0o755)
        os.symlink(foreign, self.link)
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.readlink(self.link), foreign)  # untouched

    def test_dangling_foreign_symlink_named_ambient_is_never_relinked(self):
        # A DANGLING symlink to a foreign `ambient` (no ambient-codex component)
        # must also be left alone — readlink still exposes the stored target.
        foreign = os.path.join(self.home, "gone-tool", "bin", "ambient")
        os.symlink(foreign, self.link)
        self.assertFalse(os.path.exists(self.link))  # dangling
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertEqual(os.readlink(self.link), foreign)  # untouched

    def test_nothing_present_creates_nothing(self):
        self.assertFalse(os.path.lexists(self.link))
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertFalse(os.path.lexists(self.link))

    def test_claude_ambient_code_symlink_is_never_repointed(self):
        plugin_cache = os.path.dirname(os.path.dirname(self.root))
        claude_root = os.path.join(plugin_cache, "ambient-code", "1.3.0", "bin")
        os.makedirs(claude_root)
        claude_bin = os.path.join(claude_root, "ambient")
        shutil.copyfile(BIN, claude_bin)
        os.chmod(claude_bin, 0o755)
        os.symlink(claude_bin, self.link)
        proc = self._run_hook()
        self._assert_silent_ok(proc)
        self.assertEqual(os.readlink(self.link), claude_bin)

    def test_claude_plugin_root_env_is_ignored(self):
        stale_root = os.path.join(os.path.dirname(self.root), "1.9.0", "bin")
        os.makedirs(stale_root)
        stale = os.path.join(stale_root, "ambient")
        shutil.copyfile(BIN, stale)
        os.chmod(stale, 0o755)
        os.symlink(stale, self.link)
        proc = self._run_hook(plugin_root=False, claude_plugin_root=self.root)
        self._assert_silent_ok(proc)
        self.assertEqual(os.readlink(self.link), stale)


if __name__ == "__main__":
    unittest.main()
