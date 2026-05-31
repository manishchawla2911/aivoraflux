from pydantic import BaseModel, ConfigDict, Field


class PRDCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements_covered: list[str]
    requirements_missing: list[str]
    coverage_percent: float


class GateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests_passed: bool
    security_passed: bool
    quality_passed: bool
    all_files_present: bool


class FlagForHuman(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_type: str
    description: str
    options: list[str]
    recommended_option: str


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    overall_status: str  # "auto_approved" | "needs_human" | "failed"
    prd_coverage: PRDCoverage
    gate_summary: GateSummary
    flags_for_human: list[FlagForHuman] = Field(default_factory=list)
    auto_approval_rationale: str = ""
    merge_ready: bool


class DocumentsGenerated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_docs: str
    setup_guide: str
    architecture_overview: str
    runbook: str
    env_template: str


class DeliveryPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    documents_generated: DocumentsGenerated
    proof_report_path: str
    deployment_scripts: list[str]
    delivery_package_path: str
