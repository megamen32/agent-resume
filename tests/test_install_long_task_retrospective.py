from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-long-task-retrospective.py"
SPEC = importlib.util.spec_from_file_location("installer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstallerTests(unittest.TestCase):
    def test_install_is_idempotent_and_backs_up_different_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.md"
            source.write_text("new\n", encoding="utf-8")
            target_root = root / "skills"

            first = MODULE.install(source, target_root)
            self.assertTrue(first["changed"])
            self.assertIsNone(first["backup"])

            second = MODULE.install(source, target_root)
            self.assertFalse(second["changed"])

            target = target_root / MODULE.SKILL_NAME / "SKILL.md"
            target.write_text("old\n", encoding="utf-8")
            third = MODULE.install(source, target_root)
            self.assertTrue(third["changed"])
            self.assertIsNotNone(third["backup"])
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(Path(third["backup"]).read_text(encoding="utf-8"), "old\n")

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.md"
            source.write_text("new\n", encoding="utf-8")
            target_root = root / "skills"

            result = MODULE.install(source, target_root, dry_run=True)
            self.assertTrue(result["changed"])
            self.assertFalse((target_root / MODULE.SKILL_NAME / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
