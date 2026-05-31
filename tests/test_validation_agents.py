"""Tests for Test Writer Agent and Security & Quality Agent."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.security_quality_agent import (
    SecurityQualityAgent,
    compute_complexity_findings,
    compute_scores,
    parse_bandit_json,
    scan_secrets,
)
from agents.test_writer_agent import TestWriterAgent, parse_pytest_output, parse_vitest_json
from schemas.validation_report import Severity


# ----------------------- pytest output parser -----------------------

def test_parse_pytest_output_passes():
    text = "============== 5 passed in 0.12s =============="
    r = parse_pytest_output(text)
    assert r.passed == 5
    assert r.failed == 0


def test_parse_pytest_output_failures():
    text = (
        "FAILED tests/test_x.py::test_a - AssertionError: nope\n"
        "============== 3 passed, 1 failed in 0.5s =============="
    )
    r = parse_pytest_output(text)
    assert r.passed == 3
    assert r.failed == 1
    assert len(r.failures) == 1
    assert r.failures[0].test_name == "tests/test_x.py::test_a"


def test_parse_vitest_json():
    data = {
        "numPassedTests": 4, "numFailedTests": 1, "numPendingTests": 0,
        "testResults": [{
            "assertionResults": [
                {"status": "passed", "fullName": "ok"},
                {"status": "failed", "fullName": "broken", "failureMessages": ["boom"]},
            ],
        }],
    }
    r = parse_vitest_json(json.dumps(data))
    assert r.passed == 4
    assert r.failed == 1
    assert r.failures[0].test_name == "broken"


# ----------------------- Test Writer Agent -----------------------

async def test_test_writer_agent_reports_passed(tmp_path):
    # Stub command runner — returns a clean pytest summary
    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout="============ 7 passed in 0.1s ============",
            stderr="",
        )
    agent = TestWriterAgent(run_command=fake_run, repo_path=tmp_path)
    result = await agent.run({
        "task_id": "BE-001",
        "files_created": ["src/users.py"],
        "existing_tests": [],
        "ask_for_tests": False,
    })
    assert result.status == "success"
    assert result.payload["status"] == "passed"
    assert result.payload["test_run_result"]["passed"] == 7


async def test_test_writer_agent_reports_failed(tmp_path):
    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1,
            stdout=(
                "FAILED tests/test_x.py::test_a - AssertionError: nope\n"
                "============ 2 passed, 1 failed in 0.1s ============"
            ),
            stderr="",
        )
    agent = TestWriterAgent(run_command=fake_run, repo_path=tmp_path)
    result = await agent.run({
        "task_id": "BE-002",
        "files_created": ["src/y.py"],
        "ask_for_tests": False,
    })
    assert result.status == "needs_human"
    assert result.payload["status"] == "failed"


# ----------------------- Security & Quality Agent -----------------------

def test_scan_secrets_finds_aws_key(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    found = scan_secrets(["bad.py"], tmp_path)
    assert len(found) == 1
    assert found[0].secret_type == "aws_key"


def test_scan_secrets_finds_hardcoded_password(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text('password = "supersecret123"\n')
    found = scan_secrets(["bad.py"], tmp_path)
    assert any(s.secret_type == "password_in_code" for s in found)


def test_scan_secrets_clean_file(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    assert scan_secrets(["clean.py"], tmp_path) == []


def test_compute_complexity_flags_high_complexity(tmp_path):
    f = tmp_path / "complex.py"
    f.write_text("""
def complex_one():
    if a: pass
    if b: pass
    if c: pass
    if d: pass
    if e: pass
    if f: pass
    if g: pass
    if h: pass
    if i: pass
    if j: pass
    if k: pass
""")
    out = compute_complexity_findings(["complex.py"], tmp_path)
    assert len(out) == 1
    assert "complexity" in out[0].description


def test_compute_complexity_clean_file(tmp_path):
    f = tmp_path / "simple.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    assert compute_complexity_findings(["simple.py"], tmp_path) == []


def test_parse_bandit_json_extracts_findings():
    data = {
        "results": [
            {
                "test_id": "B101", "filename": "x.py", "line_number": 5,
                "issue_severity": "HIGH", "issue_text": "assert used",
                "more_info": "https://...",
            },
        ],
    }
    out = parse_bandit_json(json.dumps(data))
    assert len(out) == 1
    assert out[0].severity == Severity.HIGH
    assert out[0].rule == "B101"


def test_compute_scores_penalises_findings():
    from schemas.validation_report import SecurityFinding, QualityFinding, SecretFinding
    sec = [SecurityFinding(
        severity=Severity.CRITICAL, rule="R1", file="x", line=1,
        description="d", fix_suggestion="f",
    )]
    qual = [QualityFinding(finding_type="complexity", file="x", line=1, description="d")]
    secrets = [SecretFinding(file="x", line=1, secret_type="aws_key")]
    scores = compute_scores(sec, qual, secrets)
    assert scores["security_score"] < 75  # critical + secret heavily penalises
    assert scores["quality_score"] == 90


async def test_security_quality_agent_clean_file(tmp_path):
    f = tmp_path / "good.py"
    f.write_text("def add(a, b):\n    return a + b\n")

    # Stub bandit / semgrep — return empty findings list.
    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"results": [], "errors": []}',
            stderr="",
        )

    agent = SecurityQualityAgent(run_command=fake_run, repo_path=tmp_path)
    result = await agent.run({
        "task_id": "BE-001",
        "files_created": ["good.py"],
        "agent_type": "backend",
    })
    assert result.status == "success"
    assert result.payload["status"] == "passed"
    assert result.payload["scores"]["security_score"] >= 75


async def test_security_quality_agent_bad_file_with_secret(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text(
        'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n'
        'def f():\n'
        '    if a: pass\n' * 1 +
        ""
    )

    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout='{"results": []}', stderr="",
        )

    agent = SecurityQualityAgent(run_command=fake_run, repo_path=tmp_path)
    result = await agent.run({
        "task_id": "BE-002",
        "files_created": ["bad.py"],
        "agent_type": "backend",
    })
    assert result.status == "needs_human"
    assert result.payload["status"] == "failed"
    assert result.payload["secret_scan"]["secrets_found"] is True


async def test_security_quality_agent_with_bandit_high_finding(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def f(): return 1\n")

    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1,
            stdout=json.dumps({
                "results": [{
                    "test_id": "B102", "filename": "code.py", "line_number": 1,
                    "issue_severity": "HIGH", "issue_text": "exec used",
                    "more_info": "",
                }],
            }),
            stderr="",
        )

    agent = SecurityQualityAgent(run_command=fake_run, repo_path=tmp_path)
    result = await agent.run({
        "task_id": "BE-003",
        "files_created": ["code.py"],
        "agent_type": "backend",
    })
    assert result.status == "needs_human"
    assert result.payload["status"] == "failed"
    assert len(result.payload["blocking_findings"]) == 1
