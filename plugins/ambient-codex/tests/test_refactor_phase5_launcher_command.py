"""Phase 5 contracts for the stable launcher command."""

import argparse
import importlib
import os
import tempfile
import unittest


class LauncherCommandTests(unittest.TestCase):
    def setUp(self):
        self.core = importlib.import_module("ambient_codex.launcher_command")

    def test_foreign_regular_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "ambient-codex")
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write("foreign")
            writes = []
            deps = self.core.LauncherDependencies(
                launcher_name="ambient-codex",
                stable_launcher_marker="ambient-codex stable launcher v1",
                link_is_ours=lambda _path: False,
                shim_is_ours=lambda _path: False,
                stable_launcher_asset=lambda: "/unused",
                stable_launcher_is_ours=lambda _path: False,
                write_stable_launcher=lambda _src, _dst: writes.append(_dst),
            )

            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                self.core.run_link(
                    argparse.Namespace(dir=root, remove=False), deps)

            self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
