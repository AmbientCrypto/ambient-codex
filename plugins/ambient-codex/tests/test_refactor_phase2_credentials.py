"""Phase 2B1 contracts for credential backend isolation."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = (
    "secret_backend",
    "keychain_available",
    "keychain_read",
    "keychain_write",
    "keychain_delete",
    "shared_key_env_is_set",
    "resolve_key_and_backend",
)


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2b1", str(BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class InternalCredentialTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        credentials = importlib.import_module("ambient_codex.credentials")

        self.assertEqual(credentials.__all__, MOVED_NAMES)

    def test_backend_selection_is_explicit_and_platform_bounded(self):
        credentials = importlib.import_module("ambient_codex.credentials")
        available = {"security", "secret-tool"}

        def lookup(name):
            return f"/bin/{name}" if name in available else None

        self.assertEqual(credentials.secret_backend("darwin", lookup), "keychain")
        self.assertEqual(credentials.secret_backend("linux", lookup), "secret-tool")
        self.assertIsNone(credentials.secret_backend("win32", lookup))
        self.assertFalse(credentials.keychain_available(None))
        self.assertFalse(credentials.keychain_available("unsupported"))
        self.assertTrue(credentials.keychain_available("keychain"))

    def test_reads_use_only_the_explicit_service_and_account(self):
        credentials = importlib.import_module("ambient_codex.credentials")
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="  secret-value\n")

        value = credentials.keychain_read(
            "keychain", run, "ambient-codex", "api-key"
        )
        self.assertEqual(value, "secret-value")
        self.assertEqual(
            calls[0][0],
            ["security", "find-generic-password", "-s", "ambient-codex",
             "-a", "api-key", "-w"],
        )
        self.assertEqual(calls[0][1]["timeout"], 10)

        calls.clear()
        value = credentials.keychain_read(
            "secret-tool", run, "ambient-codex", "api-key"
        )
        self.assertEqual(value, "secret-value")
        self.assertEqual(
            calls[0][0],
            ["secret-tool", "lookup", "service", "ambient-codex",
             "account", "api-key"],
        )
        self.assertIsNone(
            credentials.keychain_read(None, run, "ambient-codex", "api-key")
        )

    def test_writes_keep_the_secret_out_of_process_argv(self):
        credentials = importlib.import_module("ambient_codex.credentials")
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="")

        secret = "fixture-secret-value-000000"
        self.assertTrue(credentials.keychain_write(
            secret, "keychain", run, "ambient-codex", "api-key"
        ))
        argv, kwargs = calls.pop()
        self.assertEqual(argv, ["security", "-i"])
        self.assertNotIn(secret, " ".join(argv))
        self.assertIn(secret, kwargs["input"])

        self.assertTrue(credentials.keychain_write(
            secret, "secret-tool", run, "ambient-codex", "api-key"
        ))
        argv, kwargs = calls.pop()
        self.assertNotIn(secret, " ".join(argv))
        self.assertEqual(kwargs["input"], secret)

        for unsafe in ('bad"key', "bad\\key", "bad\nkey", "bad\rkey", "", None, 42):
            with self.subTest(unsafe=repr(unsafe)):
                self.assertFalse(credentials.keychain_write(
                    unsafe, "keychain", run, "ambient-codex", "api-key"
                ))

        for service, account in (
            ('bad"service', "api-key"),
            ("ambient-codex", "bad\naccount"),
        ):
            with self.subTest(service=service, account=account):
                before = len(calls)
                self.assertFalse(credentials.keychain_write(
                    secret, "keychain", run, service, account
                ))
                self.assertEqual(len(calls), before)

    def test_backend_failures_and_delete_statuses_are_classified(self):
        credentials = importlib.import_module("ambient_codex.credentials")

        def completed(code):
            return lambda argv, **kwargs: subprocess.CompletedProcess(argv, code)

        self.assertTrue(credentials.keychain_delete(
            "keychain", completed(0), "ambient-codex", "api-key"
        ))
        self.assertTrue(credentials.keychain_delete(
            "keychain", completed(44), "ambient-codex", "api-key"
        ))
        self.assertFalse(credentials.keychain_delete(
            "keychain", completed(1), "ambient-codex", "api-key"
        ))
        self.assertTrue(credentials.keychain_delete(
            "secret-tool", completed(0), "ambient-codex", "api-key"
        ))
        self.assertFalse(credentials.keychain_delete(
            "secret-tool", completed(1), "ambient-codex", "api-key"
        ))
        self.assertTrue(credentials.keychain_delete(
            None, completed(1), "ambient-codex", "api-key"
        ))
        self.assertFalse(credentials.keychain_write(
            "safe-key", None, completed(0), "ambient-codex", "api-key"
        ))

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 10)

        self.assertIsNone(credentials.keychain_read(
            "keychain", timeout, "ambient-codex", "api-key"
        ))
        self.assertFalse(credentials.keychain_write(
            "safe-key", "keychain", timeout, "ambient-codex", "api-key"
        ))
        self.assertFalse(credentials.keychain_delete(
            "keychain", timeout, "ambient-codex", "api-key"
        ))

    def test_key_precedence_never_adopts_the_shared_key(self):
        credentials = importlib.import_module("ambient_codex.credentials")
        reads = []

        def read_keychain():
            reads.append(True)
            return "stored-key"

        self.assertEqual(
            credentials.resolve_key_and_backend(
                {}, "env-key", read_keychain, "keychain"
            ),
            ("env-key", "env"),
        )
        self.assertEqual(reads, [])
        self.assertEqual(
            credentials.resolve_key_and_backend(
                {"AMBIENT_KEY_BACKEND": "file", "AMBIENT_API_KEY": "file-key"},
                None, read_keychain, "keychain",
            ),
            ("file-key", "file"),
        )
        self.assertEqual(reads, [])
        self.assertEqual(
            credentials.resolve_key_and_backend(
                {}, None, read_keychain, "keychain"
            ),
            ("stored-key", "keychain"),
        )
        self.assertEqual(
            credentials.resolve_key_and_backend(
                {}, None, lambda: None, "keychain"
            ),
            (None, None),
        )
        self.assertTrue(credentials.shared_key_env_is_set("other-install-key"))
        self.assertFalse(credentials.shared_key_env_is_set(None))

    def test_internal_import_has_no_external_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = dict(os.environ)
            env.update({
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT),
            })
            proc = subprocess.run(
                [sys.executable, "-c", "import ambient_codex.credentials"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeCredentialTests(unittest.TestCase):
    def test_facade_preserves_patchable_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            completed = subprocess.CompletedProcess([], 44, stdout="", stderr="")
            with mock.patch.object(facade, "secret_backend", return_value="keychain"), \
                 mock.patch.object(facade.subprocess, "run", return_value=completed) as run:
                self.assertTrue(facade.keychain_delete())
            self.assertIn("ambient-codex", run.call_args.args[0])

            with mock.patch.dict(os.environ, {facade.API_KEY_ENV: "env-key"}), \
                 mock.patch.object(facade, "keychain_read") as read:
                self.assertEqual(
                    facade.resolve_key_and_backend({}), ("env-key", "env")
                )
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
