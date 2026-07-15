"""Phase 4 contracts for transactional build-file application."""

import hashlib
import importlib
import os
import tempfile
import unittest


class BuildApplyTests(unittest.TestCase):
    def test_create_unchanged_skip_and_force_backup_are_deterministic(self):
        core = importlib.import_module("ambient_codex.build_apply")
        content = "print('new')\n"
        done = {"src/app.py": {
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }}

        with tempfile.TemporaryDirectory() as root:
            within = lambda child, parent: os.path.commonpath(  # noqa: E731
                [child, parent]) == parent
            actions, failures = core.apply_records(
                done, root, force=False, backup_stamp="stamp",
                within_root=within)
            self.assertEqual(actions, (("src/app.py", "create"),))
            self.assertEqual(failures, ())

            actions, failures = core.apply_records(
                done, root, force=False, backup_stamp="stamp",
                within_root=within)
            self.assertEqual(actions, (("src/app.py", "unchanged"),))
            self.assertEqual(failures, ())

            path = os.path.join(root, "src", "app.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("old")
            actions, failures = core.apply_records(
                done, root, force=False, backup_stamp="stamp",
                within_root=within)
            self.assertEqual(actions, (("src/app.py", "skip-exists"),))
            self.assertEqual(failures, ())

            actions, failures = core.apply_records(
                done, root, force=True, backup_stamp="stamp",
                within_root=within)
            self.assertEqual(actions, (("src/app.py", "overwrite"),))
            self.assertEqual(failures, ())
            backup = os.path.join(
                root, ".ambient-build.bak", "stamp", "src", "app.py")
            with open(backup, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), content)


if __name__ == "__main__":
    unittest.main()
