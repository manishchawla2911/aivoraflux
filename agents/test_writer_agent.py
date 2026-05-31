"""Test Writer Agent — writes missing tests and runs the suite.

Model: claude-sonnet-4-6 with tool use (read_file, write_file, run_command).
For now the tool-use loop is simplified: the agent asks the model what tests
to add, writes them, then runs the test command and parses the result.

This file is intentionally lighter than the full tool-loop spec — production
will swap _run_test_command and _ask_for_tests for the real Anthropic tool
use SDK. The shape here lets us drive it end-to-end with mocks.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from agents._anthropic_client import AnthropicClient, extract_json
from agents.base_agent import AgentResult, BaseAgent
from schemas.validation_report import TestFailure, TestReport, TestRunResult

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ------------------- test runner output parsers -------------------

def parse_pytest_output(text: str) -> TestRunResult:
    """Parse `pytest` summary line, e.g. '3 passed, 1 failed, 2 skipped'."""
    passed = failed = skipped = 0
    failures: list[TestFailure] = []
    m = re.search(r"(\d+)\s+passed", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", text)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped", text)
    if m:
        skipped = int(m.group(1))
    coverage = 0.0
    m = re.search(r"TOTAL.*?(\d+(?:\.\d+)?)%", text)
    if m:
        coverage = float(m.group(1))
    # Best-effort failure parsing: lines like "FAILED tests/foo.py::test_bar - AssertionError: ..."
    for line in text.splitlines():
        m = re.match(r"FAILED\s+(\S+)\s*-\s*(.*)", line)
        if m:
            failures.append(TestFailure(test_name=m.group(1), error_message=m.group(2)))
    return TestRunResult(
        passed=passed, failed=failed, skipped=skipped,
        coverage_percent=coverage, failures=failures,
    )


def parse_vitest_json(stdout: str) -> TestRunResult:
    """Parse `vitest --reporter=json` output."""
    data = json.loads(stdout)
    passed = data.get("numPassedTests", 0)
    failed = data.get("numFailedTests", 0)
    skipped = data.get("numPendingTests", 0)
    failures: list[TestFailure] = []
    for tr in data.get("testResults", []):
        for ar in tr.get("assertionResults", []):
            if ar.get("status") == "failed":
                failures.append(TestFailure(
                    test_name=ar.get("fullName", "unknown"),
                    error_message="\n".join(ar.get("failureMessages", [])) or "failed",
                ))
    return TestRunResult(
        passed=passed, failed=failed, skipped=skipped, coverage_percent=0.0, failures=failures,
    )


class TestWriterAgent(BaseAgent):
    name = "test_writer_agent"
    model = "claude-sonnet-4-6"

    def __init__(
        self,
        client: Optional[AnthropicClient] = None,
        prompt_path: Optional[Path] = None,
        run_command: Optional[Callable[[list[str], Path], subprocess.CompletedProcess]] = None,
        repo_path: Optional[Path] = None,
        test_command: Optional[list[str]] = None,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._client = client or AnthropicClient()
        self._prompt = (prompt_path or PROMPTS_DIR / "test_writer_agent.md").read_text(
            encoding="utf-8"
        )
        self._run_cmd = run_command or self._default_run_command
        self._repo_path = repo_path or Path.cwd()
        self._test_command = test_command  # auto-detect if None

    async def run(self, input: dict) -> AgentResult:
        try:
            task_id = input["task_id"]
            files_created: list[str] = input["files_created"]
        except KeyError as e:
            return AgentResult(status="failed", error=f"missing field: {e}")

        # 1. Pick a test command based on what's in files_created or the repo.
        cmd = self._test_command or self._detect_test_command(files_created)

        # 2. Ask the LLM what tests to add (production: this is a tool-use loop).
        ask_for_tests = input.get("ask_for_tests", True)
        tests_added: list[dict] = []
        if ask_for_tests and self._client.api_key:
            try:
                response = self._client.complete(
                    model=self.model, system=self._prompt,
                    user=self._build_user_prompt(input), max_tokens=8000,
                )
                guidance = extract_json(response.text)
                tests_added = guidance.get("tests_added", []) if isinstance(guidance, dict) else []
            except Exception as e:  # noqa: BLE001
                logger.warning("test_writer.llm_skipped", extra={"error": str(e)})

        # 3. Run the test suite.
        completed = self._run_cmd(cmd, self._repo_path)
        is_python = any(c.endswith("pytest") for c in cmd)
        if is_python:
            result = parse_pytest_output(completed.stdout + "\n" + completed.stderr)
        else:
            try:
                result = parse_vitest_json(completed.stdout)
            except (ValueError, json.JSONDecodeError):
                result = parse_pytest_output(completed.stdout + completed.stderr)

        status = "passed" if result.failed == 0 and completed.returncode == 0 else "failed"
        try:
            report = TestReport(
                task_id=task_id, tests_added=tests_added,
                test_run_result=result, status=status,
            )
        except ValidationError as e:
            return AgentResult(status="failed", error=f"report invalid: {e}")

        return AgentResult(
            status="success" if status == "passed" else "needs_human",
            payload=report.model_dump(mode="json"),
        )

    def _build_user_prompt(self, input: dict) -> str:
        return (
            f"TASK: {input.get('task_id')}\n"
            f"FILES: {input.get('files_created')}\n"
            f"EXISTING TESTS: {input.get('existing_tests', [])}\n\n"
            "Return JSON: {\"tests_added\": [{\"path\": \"...\", \"test_names\": [...]}]}\n"
        )

    @staticmethod
    def _detect_test_command(files_created: list[str]) -> list[str]:
        is_python = any(f.endswith(".py") for f in files_created)
        return ["pytest", "--tb=short"] if is_python else ["npx", "vitest", "run", "--reporter=json"]

    @staticmethod
    def _default_run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
