#!/usr/bin/env python3
"""Install the bundled long-task-retrospective Codex skill."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "long-task-retrospective"


def bundled_skill() -> Path:
    """Return the skill bundled with this repository/package."""
    return Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME / "SKILL.md"


def install(source: Path, target_root: Path, dry_run: bool = False) -> dict[str, Any]:
    """Install the skill idempotently and back up a differing existing copy."""
    if not source.is_file():
        raise FileNotFoundError(f"Bundled skill not found: {source}")

    target_dir = target_root.expanduser() / SKILL_NAME
    target = target_dir / "SKILL.md"
    source_text = source.read_text(encoding="utf-8")
    existing_text = target.read_text(encoding="utf-8") if target.exists() else None

    result: dict[str, Any] = {
        "ok": True,
        "skill": SKILL_NAME,
        "source": str(source),
        "target": str(target),
        "changed": existing_text != source_text,
        "backup": None,
        "dry_run": dry_run,
    }
    if existing_text == source_text or dry_run:
        return result

    target_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"SKILL.md.bak.{stamp}")
        shutil.copy2(target, backup)
        result["backup"] = str(backup)

    target.write_text(source_text, encoding="utf-8")
    return result


def main() -> None:
    """Parse CLI arguments, install the skill, and emit JSON status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-root",
        default="~/.agents/skills",
        help="Skill root directory (default: ~/.agents/skills)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = install(bundled_skill(), Path(args.target_root), args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
