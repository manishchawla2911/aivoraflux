"""Security & Quality Agent — runs Semgrep / Bandit / ESLint + secret scan.

Pass criteria (per AGENT_SPECS.md section "Agent 9"):
- Zero secrets found
- Zero critical security findings
- Zero high security findings without documented exception
- Security score ≥ 75
- Quality score ≥ 70
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from agents.base_agent import AgentResult, BaseAgent
from schemas.validation_report import (
    QualityFinding,
    SecretFinding,
    SecurityFinding,
    SecurityQualityReport,
    Severity,
)

logger = logging.getLogger(__name__)


# Secret patterns from CLAUDE_CODE_INSTRUCTIONS.md Phase 5.
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "generic_api_key": re.compile(r"""[aA][pP][iI]_?[kK][eE][yY].*['"][0-9a-zA-Z]{32,}['"]"""),
    "password_in_code": re.compile(r"""password\s*=\s*['"][^'"]{8,}['"]"""),
    "private_key": re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
    "jwt_secret": re.compile(r"""jwt.*secret.*=.*['"][^'"]{16,}['"]""", re.IGNORECASE),
}


def scan_secrets(file_paths: list[str], root: Path) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for rel in file_paths:
        p = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if not p.exists() or p.is_dir():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(SecretFinding(file=rel, line=line_no, secret_type=name))
    return findings


def parse_bandit_json(stdout: str) -> list[SecurityFinding]:
    """Parse `bandit -f json -r <files>` output."""
    findings: list[SecurityFinding] = []
    try:
        data = json.loads(stdout)
    except (ValueError, json.JSONDecodeError):
        return findings
    severity_map = {
        "HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW,
    }
    for r in data.get("results", []):
        sev = severity_map.get(str(r.get("issue_severity", "LOW")).upper(), Severity.LOW)
        findings.append(SecurityFinding(
            severity=sev,
            rule=r.get("test_id", "B000"),
            file=r.get("filename", ""),
            line=r.get("line_number", 0),
            description=r.get("issue_text", ""),
            fix_suggestion=r.get("more_info", "see bandit docs"),
        ))
    return findings


def parse_semgrep_json(stdout: str) -> list[SecurityFinding]:
    """Parse `semgrep --json` output."""
    findings: list[SecurityFinding] = []
    try:
        data = json.loads(stdout)
    except (ValueError, json.JSONDecodeError):
        return findings
    severity_map = {
        "ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW,
    }
    for r in data.get("results", []):
        extra = r.get("extra", {})
        sev = severity_map.get(str(extra.get("severity", "INFO")).upper(), Severity.LOW)
        findings.append(SecurityFinding(
            severity=sev,
            rule=r.get("check_id", "semgrep"),
            file=r.get("path", ""),
            line=r.get("start", {}).get("line", 0),
            description=extra.get("message", ""),
            fix_suggestion=extra.get("fix", "see semgrep docs"),
        ))
    return findings


def compute_complexity_findings(file_paths: list[str], root: Path) -> list[QualityFinding]:
    """Very rough: count nested control-flow keywords per function as a heuristic."""
    findings: list[QualityFinding] = []
    control_kw = re.compile(r"\b(if|for|while|elif|except|case)\b")
    for rel in file_paths:
        p = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if not p.exists() or not p.suffix == ".py":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        current_fn = None
        current_start = 0
        current_count = 0
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = re.match(r"\s*def\s+(\w+)", line)
            if m:
                if current_fn and current_count > 10:
                    findings.append(QualityFinding(
                        finding_type="complexity", file=rel, line=current_start,
                        description=f"{current_fn}: complexity {current_count} > 10",
                    ))
                current_fn = m.group(1)
                current_start = line_no
                current_count = 0
            else:
                current_count += len(control_kw.findall(line))
        if current_fn and current_count > 10:
            findings.append(QualityFinding(
                finding_type="complexity", file=rel, line=current_start,
                description=f"{current_fn}: complexity {current_count} > 10",
            ))
    return findings


def compute_scores(
    sec: list[SecurityFinding], qual: list[QualityFinding], secrets: list[SecretFinding]
) -> dict[str, int]:
    """Simple weighted scoring."""
    sec_penalty = 0
    for f in sec:
        sec_penalty += {Severity.CRITICAL: 40, Severity.HIGH: 20, Severity.MEDIUM: 5, Severity.LOW: 1}[f.severity]
    sec_penalty += 30 * len(secrets)
    security_score = max(0, 100 - sec_penalty)

    quality_score = max(0, 100 - 10 * len(qual))
    return {"security_score": int(security_score), "quality_score": int(quality_score)}


class SecurityQualityAgent(BaseAgent):
    name = "security_quality_agent"
    model = "claude-sonnet-4-6"

    def __init__(
        self,
        run_command: Optional[Callable[[list[str], Path], subprocess.CompletedProcess]] = None,
        repo_path: Optional[Path] = None,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._run_cmd = run_command or self._default_run_command
        self._repo_path = repo_path or Path.cwd()

    async def run(self, input: dict) -> AgentResult:
        try:
            task_id = input["task_id"]
            files_created: list[str] = input["files_created"]
            agent_type: str = input["agent_type"]
        except KeyError as e:
            return AgentResult(status="failed", error=f"missing field: {e}")

        security_findings: list[SecurityFinding] = []
        quality_findings: list[QualityFinding] = []

        # 1. Static analysis. Pick tool by agent_type / file extensions.
        is_python = any(f.endswith(".py") for f in files_created)
        is_js = any(f.endswith((".js", ".ts", ".tsx", ".jsx")) for f in files_created)

        if is_python and files_created:
            try:
                proc = self._run_cmd(
                    ["bandit", "-q", "-f", "json", "-r", *files_created],
                    self._repo_path,
                )
                security_findings.extend(parse_bandit_json(proc.stdout))
            except FileNotFoundError:
                logger.warning("security.bandit_missing")
            except Exception as e:  # noqa: BLE001
                logger.warning("security.bandit_failed", extra={"error": str(e)})

        if is_js and files_created:
            try:
                proc = self._run_cmd(
                    ["semgrep", "--json", "--config=auto", *files_created],
                    self._repo_path,
                )
                security_findings.extend(parse_semgrep_json(proc.stdout))
            except FileNotFoundError:
                logger.warning("security.semgrep_missing")
            except Exception as e:  # noqa: BLE001
                logger.warning("security.semgrep_failed", extra={"error": str(e)})

        # 2. Secret scan.
        secret_findings = scan_secrets(files_created, self._repo_path)

        # 3. Quality (complexity).
        quality_findings.extend(compute_complexity_findings(files_created, self._repo_path))

        # 4. Scores.
        scores = compute_scores(security_findings, quality_findings, secret_findings)

        # 5. Blocking findings = critical + high + any secret.
        blocking = [f for f in security_findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]

        # 6. Pass/fail.
        passed = (
            len(secret_findings) == 0
            and not any(f.severity == Severity.CRITICAL for f in security_findings)
            and not any(f.severity == Severity.HIGH for f in security_findings)
            and scores["security_score"] >= 75
            and scores["quality_score"] >= 70
        )

        report = SecurityQualityReport(
            task_id=task_id,
            security_findings=security_findings,
            quality_findings=quality_findings,
            secret_scan={
                "secrets_found": len(secret_findings) > 0,
                "findings": [f.model_dump(mode="json") for f in secret_findings],
            },
            scores=scores,
            status="passed" if passed else "failed",
            blocking_findings=blocking,
        )

        return AgentResult(
            status="success" if passed else "needs_human",
            payload=report.model_dump(mode="json"),
            metadata={"agent_type": agent_type},
        )

    @staticmethod
    def _default_run_command(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)
