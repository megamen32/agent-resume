"""Regression coverage for one-shot wait job status semantics."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


def load_agent_resume() -> Any:
    """Load the CLI module without executing its command-line entry point."""
    source = Path(__file__).parents[1] / "agent_resume.py"
    spec = importlib.util.spec_from_file_location("agent_resume_under_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class WaitJobStatusTests(unittest.TestCase):
    """Keep timer watcher state distinct from a watched command PID."""

    def test_timer_reports_live_watcher_without_watched_process(self) -> None:
        """A timer has no process PID, but its detached watcher can be alive."""
        agent_resume = load_agent_resume()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            job_meta = state_dir / "jobs" / "timer-job" / "meta.json"
            job_meta.parent.mkdir(parents=True)
            agent_resume.write_json(
                job_meta,
                {
                    "job_id": "timer-job",
                    "kind": "timer",
                    "state": "running",
                    "watcher_pid": 4321,
                },
            )

            with patch.object(agent_resume, "STATE_DIR", state_dir), patch.object(
                agent_resume,
                "is_pid_alive",
                side_effect=lambda process_id: process_id == 4321,
            ):
                status = agent_resume.tool_wait_job_status({"job_id": "timer-job"})

        self.assertFalse(status["alive"])
        self.assertFalse(status["watched_pid_alive"])
        self.assertTrue(status["watcher_alive"])
