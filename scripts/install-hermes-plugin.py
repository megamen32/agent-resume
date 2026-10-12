#!/usr/bin/env python3
"""Install the agent-resume Hermes plugin as a source symlink.

The running Gateway imports this exact checkout, so updating agent-resume never
leaves a stale second copy of the wake transport in ~/.hermes/plugins.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


MANIFEST = """name: agent-resume\nversion: 0.1.11\ndescription: Exact-session wake and authenticated Gateway task bridge.\nauthor: agent-resume\nhooks:\n  - pre_gateway_dispatch\n  - pre_llm_call\n  - post_api_request\n  - transform_tool_result\n"""


def configure_gateway_tasks(home: Path, session_key: str) -> None:
    """Explicit installation grant; merge only this plugin's supported settings.

    Never replace live config with a historical source snapshot or print its secrets.
    """
    import yaml
    if not session_key.startswith("agent:") or any(ord(c) < 32 for c in session_key):
        raise ValueError("An exact native Gateway session key is required")
    config_file = home / "config.yaml"
    config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    plugins = config.setdefault("plugins", {})
    enabled = plugins.setdefault("enabled", [])
    if "agent-resume" not in enabled:
        enabled.append("agent-resume")
    disabled = plugins.setdefault("disabled", [])
    if "agent-resume" in disabled:
        disabled.remove("agent-resume")
    entry = plugins.setdefault("entries", {}).setdefault("agent-resume", {})
    entry.update(allow_gateway_injection=True, gateway_task_session_key=session_key)
    # Atomic replacement at the resolved canonical path, preserving symlinks.
    target = config_file.resolve()
    temporary = target.with_name(target.name + ".agent-resume.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=True)
    temporary.replace(target)


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
    parser.add_argument("--gateway-session-key", help="Explicitly enable supported Gateway injection for this native home session")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "hermes_gateway.py"
    home = Path(args.hermes_home).expanduser()
    if args.gateway_session_key:
        configure_gateway_tasks(home, args.gateway_session_key)
    print(install(home, source))


if __name__ == "__main__":
    main()
