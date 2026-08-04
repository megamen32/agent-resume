"""Regression coverage for the strict, preselected resume transport."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


def load_agent_resume() -> Any:
    source = Path(__file__).parents[1] / "agent_resume.py"
    spec = importlib.util.spec_from_file_location("agent_resume_bound_target", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class ResumeBoundTargetTests(unittest.TestCase):
    def test_uses_only_frozen_identity_for_each_supported_agent(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            target_cwd = str(Path(temporary_directory).resolve())
            for agent in ("codex", "opencode", "claude"):
                with self.subTest(agent=agent), patch.object(agent_resume, "find_sessions", side_effect=AssertionError("must not search")):
                    receipt = agent_resume.resume_bound_target({
                        "target": {
                            "agent": agent,
                            "session_id": f"{agent}-session",
                            "cwd": target_cwd,
                            "marker": "A7kQ2",
                            "model": "frozen-model" if agent == "codex" else None,
                        },
                        "prompt": "continue the selected task",
                        "result_ref": f"result://{agent}",
                        "execute": False,
                    })

                self.assertEqual(receipt["status"], "accepted")
                self.assertEqual(receipt["target"]["agent"], agent)
                self.assertEqual(receipt["target"]["session_id"], f"{agent}-session")
                self.assertEqual(receipt["target"]["cwd"], target_cwd)
                self.assertFalse(receipt["executed"])
                self.assertNotIn("--last", receipt["command"])
                if agent == "codex":
                    self.assertIn("--skip-git-repo-check", receipt["command"])

    def test_rejects_missing_bound_target_without_fallback_lookup(self) -> None:
        agent_resume = load_agent_resume()
        with patch.object(agent_resume, "find_sessions", side_effect=AssertionError("must not search")):
            receipt = agent_resume.resume_bound_target({"prompt": "continue", "result_ref": "result://missing", "execute": False})

        self.assertEqual(receipt["status"], "failed")
        self.assertIn("target", receipt["error"])
        self.assertIsNone(receipt["target"])

    def test_returns_failed_receipt_when_launch_is_rejected(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = {"agent": "claude", "session_id": "claude-session", "cwd": temporary_directory}
            with patch.object(agent_resume, "STATE_DIR", Path(temporary_directory) / "state"), patch.object(
                agent_resume.subprocess, "run", return_value=type("Launch", (), {
                    "returncode": 23, "stdout": "", "stderr": "rejected",
                })(),
            ):
                receipt = agent_resume.resume_bound_target({"target": target, "prompt": "continue", "result_ref": "result://claude", "execute": True})

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["target"]["session_id"], "claude-session")
        self.assertEqual(receipt["launch_returncode"], 23)

    def test_hermes_uses_only_the_bound_locator_and_matching_receipt(self) -> None:
        agent_resume = load_agent_resume()
        target = {"agent": "hermes", "locator": {
            "schema": "hermes.locator.v2", "session_key": "agent:main:telegram:dm:42",
            "platform": "telegram", "chat_id": "42", "chat_type": "dm",
        }}
        result_ref = "7dca6552-77e2-4699-9871-b86c963f6425"
        class Adapter:
            def __init__(self, endpoint, token):
                self.endpoint, self.token = endpoint, token
            def wake(self, locator, ref):
                self.locator, self.ref = locator, ref
                return {"status": "accepted", "receipt_id": "wake-1", "target": locator, "result_ref": ref}
        gateway_source = Path(__file__).parents[1] / "hermes_gateway.py"
        gateway_spec = importlib.util.spec_from_file_location("hermes_gateway", gateway_source)
        assert gateway_spec and gateway_spec.loader
        hermes_gateway = importlib.util.module_from_spec(gateway_spec)
        with patch.dict("sys.modules", {"hermes_gateway": hermes_gateway}):
            gateway_spec.loader.exec_module(hermes_gateway)
            with patch.dict("os.environ", {"AGENT_RESUME_HERMES_ENDPOINT": "http://127.0.0.1:18791/v1/agent-resume/wake", "AGENT_RESUME_HERMES_TOKEN": "test-token"}), patch.object(hermes_gateway, "HermesGatewayAdapter", Adapter):
                receipt = agent_resume.resume_bound_target({"target": target, "result_ref": result_ref, "execute": True})
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["target"], target)
        self.assertEqual(receipt["result_ref"], result_ref)


if __name__ == "__main__":
    unittest.main()
