#!/usr/bin/env python3
"""Install the agent-resume Hermes plugin as a source symlink.

The running Gateway imports this exact checkout, so updating agent-resume never
leaves a stale second copy of the wake transport in ~/.hermes/plugins.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


MANIFEST = """name: agent-resume\nversion: 0.1.8\ndescription: Exact-session wake bridge owned by agent-resume.\nauthor: agent-resume\nhooks:\n  - pre_gateway_dispatch\n"""


def install(home: Path, source: Path) -> Path:
    plugin = home / "plugins" / "agent-resume"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    target = plugin / "__init__.py"
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(source)
    return plugin


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", "~/.hermes"))
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "hermes_gateway.py"
    print(install(Path(args.hermes_home).expanduser(), source))


if __name__ == "__main__":
    main()
