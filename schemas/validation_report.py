from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SecurityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    rule: str
    file: str
    line: int
    description: str
    fix_suggestion: str


class QualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: str  # "complexity" | "dead_code" | "style"
    file: str
    line: int
    description: str


class SecretFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int
    secret_type: str


class TestFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_name: str
    error_message: str


class TestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int
    failed: int
    skipped: int
    coverage_percent: float
    failures: list[TestFailure] = Field(default_factory=list)


class TestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    tests_added: list[dict]  # {path, test_names}
    test_run_result: TestRunResult
    status: str  # "passed" | "failed"


class SecurityQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    security_findings: list[SecurityFinding] = Field(default_factory=list)
    quality_findings: list[QualityFinding] = Field(default_factory=list)
    secret_scan: dict  # {secrets_found, findings}
    scores: dict  # {security_score, quality_score}
    status: str  # "passed" | "failed"
    blocking_findings: list[SecurityFinding] = Field(default_factory=list)
