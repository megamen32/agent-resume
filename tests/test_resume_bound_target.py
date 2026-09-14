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
    def _target(self, cwd: str, agent: str = "claude") -> dict[str, Any]:
        return {"agent": agent, "session_id": f"{agent}-session", "cwd": cwd}

    def test_uses_only_frozen_identity_for_each_supported_agent(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            target_cwd = str(Path(temporary_directory).resolve())
            for agent in ("codex", "opencode", "claude"):
                with self.subTest(agent=agent), patch.object(agent_resume, "find_sessions", side_effect=AssertionError("must not search")):
                    receipt = agent_resume.resume_bound_target({
                        "target": {
                            "agent": agent,
                            "harness": agent,
                            "session_id": f"{agent}-session",
                            "sessionId": f"{agent}-session",
                            "thread_id": f"{agent}-session",
                            "threadId": f"{agent}-session",
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

    def test_idempotency_receipt_survives_fresh_process_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            first_module = load_agent_resume()
            with patch.object(first_module, "STATE_DIR", state_dir):
                receipt = first_module.resume_bound_target({
                    "target": self._target(temporary_directory),
                    "prompt": "continue durable task",
                    "result_ref": "result://durable",
                    "idempotency_key": "resume-1",
                    "execute": False,
                })
            second_module = load_agent_resume()
            with patch.object(second_module, "STATE_DIR", state_dir):
                queried = second_module.query_resume_receipt({"idempotency_key": "resume-1"})

        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(queried, receipt)

    def test_duplicate_idempotency_key_reads_back_without_second_launch(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "123\n", "stderr": "",
            })(),
        ) as launch:
            args = {
                "target": self._target(temporary_directory),
                "prompt": "continue exactly once",
                "result_ref": "result://once",
                "idempotency_key": "resume-once",
                "execute": True,
            }
            first = agent_resume.resume_bound_target(args)
            second = agent_resume.resume_bound_target(args)

        self.assertEqual(first, second)
        self.assertEqual(launch.call_count, 1)

    def test_conflicting_idempotency_key_is_rejected_without_launch(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "123\n", "stderr": "",
            })(),
        ) as launch:
            first = agent_resume.resume_bound_target({
                "target": self._target(temporary_directory),
                "prompt": "original goal",
                "result_ref": "result://original",
                "idempotency_key": "resume-conflict",
                "execute": True,
            })
            conflict = agent_resume.resume_bound_target({
                "target": self._target(temporary_directory),
                "prompt": "different goal",
                "result_ref": "result://different",
                "idempotency_key": "resume-conflict",
                "execute": True,
            })

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason"], "idempotency_conflict")
        self.assertEqual(launch.call_count, 1)

    def test_idempotency_fingerprint_binds_canonical_cwd_and_session(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            real_cwd = Path(temporary_directory) / "real"
            real_cwd.mkdir()
            alias_cwd = Path(temporary_directory) / "alias"
            alias_cwd.symlink_to(real_cwd, target_is_directory=True)
            with patch.object(agent_resume, "STATE_DIR", Path(temporary_directory) / "state"):
                first = agent_resume.resume_bound_target({
                    "target": self._target(str(alias_cwd)),
                    "prompt": "same target",
                    "result_ref": "result://canonical",
                    "idempotency_key": "resume-canonical",
                    "execute": False,
                })
                second = agent_resume.resume_bound_target({
                    "target": self._target(str(real_cwd)),
                    "prompt": "same target",
                    "result_ref": "result://canonical",
                    "idempotency_key": "resume-canonical",
                    "execute": False,
                })

        self.assertEqual(first, second)
        self.assertEqual(first["target"]["cwd"], str(real_cwd.resolve()))

    def test_successful_detached_codex_spawn_is_accepted_and_idempotent(self) -> None:
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume, "build_resume_command", return_value=["codex", "resume"]
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "456\n", "stderr": "",
            })(),
        ) as launch:
            request = {
                "target": {**self._target(temporary_directory, "codex"), "model": "gpt-test"},
                "prompt": "continue Codex",
                "result_ref": "result://codex",
                "idempotency_key": "resume-codex",
                "execute": True,
            }
            receipt = agent_resume.resume_bound_target(request)
            replay = agent_resume.resume_bound_target(request)

        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["reason"], "resume_process_started")
        self.assertEqual(replay, receipt)
        self.assertEqual(launch.call_count, 1)

    def test_ambiguous_codex_receipt_promotes_with_later_matching_admission_proof(self) -> None:
        """A native admission proof may settle, but never relaunch, an ambiguous receipt."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory) / "state"
            request = {
                "target": {**self._target(temporary_directory, "codex"), "model": "gpt-test"},
                "prompt": "continue Codex",
                "result_ref": "result://codex-promote",
                "idempotency_key": "resume-codex-promote",
                "execute": True,
            }
            with patch.object(agent_resume, "STATE_DIR", state_dir), patch.object(
                agent_resume, "build_resume_command", return_value=["codex", "resume"]
            ), patch.object(
                agent_resume.subprocess, "run", return_value=type("Launch", (), {
                    "returncode": 0, "stdout": "789\n", "stderr": "",
                })(),
            ) as launch:
                ambiguous = agent_resume.resume_bound_target(request)
                accepted = agent_resume.resume_bound_target({
                    **request,
                    "admission_proof": {
                        "verified": True,
                        "agent": "codex",
                        "harness": "codex",
                        "session_id": "codex-session",
                        "sessionId": "codex-session",
                        "thread_id": "codex-session",
                        "threadId": "codex-session",
                    },
                })

            fresh_module = load_agent_resume()
            with patch.object(fresh_module, "STATE_DIR", state_dir):
                durable = fresh_module.query_resume_receipt({"idempotency_key": request["idempotency_key"]})

        self.assertEqual(ambiguous["status"], "accepted")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["reason"], "resume_process_started")
        self.assertEqual(accepted["receipt_id"], ambiguous["receipt_id"])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(durable, accepted)

    def test_late_conflicting_thread_proof_does_not_change_accepted_receipt(self) -> None:
        """A late proof cannot alter or relaunch an already accepted receipt."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume, "build_resume_command", return_value=["codex", "resume"]
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "654\n", "stderr": "",
            })(),
        ) as launch:
            request = {
                "target": {**self._target(temporary_directory, "codex"), "model": "gpt-test"},
                "prompt": "continue Codex",
                "result_ref": "result://codex-conflicting-thread",
                "idempotency_key": "resume-codex-conflicting-thread",
                "execute": True,
            }
            accepted = agent_resume.resume_bound_target(request)
            conflicting = agent_resume.resume_bound_target({
                **request,
                "admission_proof": {
                    "verified": True,
                    "agent": "codex",
                    "session_id": "codex-session",
                    "thread_id": "other-codex-session",
                },
            })

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(conflicting, accepted)
        self.assertEqual(launch.call_count, 1)

    def test_late_conflicting_harness_proof_does_not_change_accepted_receipt(self) -> None:
        """A late proof cannot alter or relaunch an already accepted receipt."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume, "build_resume_command", return_value=["codex", "resume"]
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "321\n", "stderr": "",
            })(),
        ) as launch:
            request = {
                "target": {**self._target(temporary_directory, "codex"), "model": "gpt-test"},
                "prompt": "continue Codex",
                "result_ref": "result://codex-conflicting-harness",
                "idempotency_key": "resume-codex-conflicting-harness",
                "execute": True,
            }
            accepted = agent_resume.resume_bound_target(request)
            conflicting = agent_resume.resume_bound_target({
                **request,
                "admission_proof": {
                    "verified": True,
                    "agent": "codex",
                    "harness": "claude",
                    "session_id": "codex-session",
                },
            })

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(conflicting, accepted)
        self.assertEqual(launch.call_count, 1)

    def test_conflicting_target_aliases_are_rejected_before_launch(self) -> None:
        """Target aliases must agree before ledger lookup can trigger a resume launch."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume, "build_resume_command", return_value=["codex", "resume"]
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "246\n", "stderr": "",
            })(),
        ) as launch:
            agent_conflict = agent_resume.resume_bound_target({
                "target": {
                    **self._target(temporary_directory, "codex"),
                    "harness": "claude",
                    "model": "gpt-test",
                },
                "prompt": "continue Codex",
                "result_ref": "result://target-agent-conflict",
                "idempotency_key": "resume-target-agent-conflict",
                "execute": True,
            })
            session_conflict = agent_resume.resume_bound_target({
                "target": {
                    **self._target(temporary_directory, "codex"),
                    "sessionId": "other-codex-session",
                    "model": "gpt-test",
                },
                "prompt": "continue Codex",
                "result_ref": "result://target-session-conflict",
                "idempotency_key": "resume-target-session-conflict",
                "execute": True,
            })

        self.assertEqual(agent_conflict["status"], "rejected")
        self.assertEqual(session_conflict["status"], "rejected")
        self.assertEqual(agent_conflict["reason"], "invalid_request")
        self.assertEqual(session_conflict["reason"], "invalid_request")
        self.assertEqual(launch.call_count, 0)

    def test_result_ref_conflict_is_rejected_without_relaunch(self) -> None:
        """A retry key may not replay a receipt for a different opaque result ref."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "135\n", "stderr": "",
            })(),
        ) as launch:
            request = {
                "target": self._target(temporary_directory),
                "prompt": "continue bound task",
                "result_ref": "result://one",
                "idempotency_key": "resume-result-ref-conflict",
                "execute": True,
            }
            first = agent_resume.resume_bound_target(request)
            conflict = agent_resume.resume_bound_target({**request, "result_ref": "result://two"})

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason"], "idempotency_conflict")
        self.assertEqual(launch.call_count, 1)

    def test_late_invalid_proof_does_not_change_accepted_codex_receipt(self) -> None:
        """A proof for another session cannot alter or relaunch an accepted receipt."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            agent_resume, "STATE_DIR", Path(temporary_directory) / "state"
        ), patch.object(
            agent_resume, "build_resume_command", return_value=["codex", "resume"]
        ), patch.object(
            agent_resume.subprocess, "run", return_value=type("Launch", (), {
                "returncode": 0, "stdout": "987\n", "stderr": "",
            })(),
        ) as launch:
            request = {
                "target": {**self._target(temporary_directory, "codex"), "model": "gpt-test"},
                "prompt": "continue Codex",
                "result_ref": "result://codex-no-promote",
                "idempotency_key": "resume-codex-no-promote",
                "execute": True,
            }
            accepted = agent_resume.resume_bound_target(request)
            replayed = agent_resume.resume_bound_target({
                **request,
                "admission_proof": {
                    "verified": True,
                    "agent": "codex",
                    "session_id": "another-codex-session",
                },
            })

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(replayed, accepted)
        self.assertEqual(launch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
