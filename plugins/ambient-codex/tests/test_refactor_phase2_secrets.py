"""Phase 2C1 contracts for the pure credential tripwire."""

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "ambient"
MOVED_NAMES = (
    "SECRET_NAMES_RE",
    "env_is_strong",
    "value_looks_nonsecret",
    "env_assignment_is_secret",
    "line_has_secret",
    "secret_hits",
)


def load_facade(home):
    prior = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update({"HOME": str(home), "USERPROFILE": str(home)})
    try:
        loader = importlib.machinery.SourceFileLoader("ambient_phase2c1", str(BIN))
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


class InternalSecretTests(unittest.TestCase):
    def test_internal_module_owns_exact_export_set(self):
        secrets = importlib.import_module("ambient_codex.secrets")

        self.assertEqual(secrets.__all__, MOVED_NAMES)

    def test_representative_true_and_false_corpus_is_preserved(self):
        secrets = importlib.import_module("ambient_codex.secrets")
        positives = (
            "AWS_SECRET_ACCESS_KEY=AbCdEf1234567890GhIjKlMn",
            "Authorization: Basic YWxpY2U6U3VwZXJTZWNyZXQxMjMheHl6",
            "REDIS_URL=redis://:supersecret1@redis.example.com:6379/0",
            "GITLAB_TOKEN=glpat-ABC123def456GHI789jkl0",
            "api_key: 'abcdef1234567890XYZ'",
            "password = supersecret_value_123",
        )
        negatives = (
            "PUBLIC_KEY=AbCdEf1234567890GhIjKlMn",
            "password = user.password_hash",
            "DB_PASSWORD=${DB_PASSWORD}",
            "existingSecret: my-app-secrets",
            "password_policy: enabled",
            "const token: CancellationToken = source.token",
        )

        self.assertTrue(all(secrets.line_has_secret(line) for line in positives))
        self.assertTrue(all(not secrets.line_has_secret(line) for line in negatives))

    def test_hits_cover_names_gutters_and_the_twenty_location_bound(self):
        secrets = importlib.import_module("ambient_codex.secrets")
        chunks = (
            (".env", "benign"),
            ("src/app.py", "  42|  7| API_TOKEN=AbCdEf1234567890GhIj\nplain"),
            ("many.txt", "\n".join(
                f"PASSWORD=SecretValue{i:02d}9" for i in range(30)
            )),
        )

        hits = secrets.secret_hits(chunks)

        self.assertEqual(hits[0], ".env (credential-named file — never send these)")
        self.assertEqual(hits[1], "src/app.py:42")
        self.assertEqual(len(hits), 20)
        self.assertIsInstance(hits, tuple)

    def test_long_adversarial_lines_remain_bounded(self):
        secrets = importlib.import_module("ambient_codex.secrets")
        started = time.monotonic()

        self.assertFalse(secrets.line_has_secret("a" * 1_000_000))

        self.assertLess(time.monotonic() - started, 1.0)

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
                [sys.executable, "-c", "import ambient_codex.secrets"],
                cwd=str(home), env=env, capture_output=True, text=True,
                timeout=60, check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(list(home.iterdir()), [])


class FacadeSecretTests(unittest.TestCase):
    def test_facade_detection_and_refusal_contract_remain_patchable(self):
        with tempfile.TemporaryDirectory() as td:
            facade = load_facade(Path(td) / "home")
            line = "API_TOKEN=AbCdEf1234567890GhIj"
            self.assertTrue(facade._line_has_secret(line))

            with mock.patch.object(facade, "_fail_exit") as fail:
                facade.refuse_if_secrets((("src/app.py", line),), allow=False)
            args = fail.call_args.args
            self.assertEqual(args[2], "secrets")
            self.assertIn("src/app.py:1", args[3])

            with mock.patch.object(facade, "_fail_exit") as fail:
                facade.refuse_if_secrets(((".env", "x"),), allow=True)
            fail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
