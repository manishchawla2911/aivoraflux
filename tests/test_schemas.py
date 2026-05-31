"""Round-trip serialisation tests for every schema."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.architecture import (
    APIContract,
    Architecture,
    ArchitectureDecision,
    ComponentType,
    DataFlow,
    DBColumn,
    DBTable,
    EnvironmentVariable,
    RiskFlag,
    SystemComponent,
    TechStack,
)
from schemas.delivery import (
    DeliveryPackage,
    DocumentsGenerated,
    FlagForHuman,
    GateSummary,
    PRDCoverage,
    ReviewSummary,
)
from schemas.prd import (
    PRD,
    ClarificationInput,
    ClarificationOutput,
    FunctionalRequirement,
    NonFunctionalRequirement,
    Priority,
    UserPersona,
    UserStory,
)
from schemas.task import (
    AgentType,
    BuilderOutput,
    ContextPackage,
    Task,
    TaskComplexity,
    TaskGraph,
    TaskStatus,
)
from schemas.validation_report import (
    QualityFinding,
    SecurityFinding,
    SecurityQualityReport,
    Severity,
    TestFailure,
    TestReport,
    TestRunResult,
)


def _round_trip(model_instance):
    """Serialize to dict, re-validate, assert equality."""
    cls = model_instance.__class__
    data = model_instance.model_dump(mode="json")
    rebuilt = cls.model_validate(data)
    assert rebuilt == model_instance
    return rebuilt


# ---------- PRD ----------

def _sample_prd() -> PRD:
    return PRD(
        project_name="Acme Portal",
        problem_statement="Manual order tracking",
        user_personas=[UserPersona(name="Op", description="Operator", primary_goal="Track")],
        user_stories=[UserStory(
            story_id="US-1",
            as_a="operator",
            i_want="dashboard",
            so_that="I track orders",
            acceptance_criteria=["loads in <2s"],
            priority=Priority.HIGH,
        )],
        functional_requirements=[FunctionalRequirement(
            req_id="FR-1", description="Show orders", priority=Priority.HIGH, linked_stories=["US-1"]
        )],
        non_functional_requirements=[NonFunctionalRequirement(category="performance", requirement="p95<500ms")],
        out_of_scope=["mobile app"],
        tech_constraints="must run on AWS",
        success_metrics=["adoption>50%"],
        edge_cases=["empty order list"],
    )


def test_prd_round_trip():
    _round_trip(_sample_prd())


def test_clarification_input_round_trip():
    inp = ClarificationInput(
        raw_brief="build an x", client_name="Acme", budget_range="$10k", timeline_weeks=4,
    )
    _round_trip(inp)


def test_clarification_output_round_trip():
    out = ClarificationOutput(status="prd_ready", prd=_sample_prd())
    _round_trip(out)
    out2 = ClarificationOutput(status="needs_clarification", clarification_questions=["Who?"])
    _round_trip(out2)


def test_extra_fields_forbidden_prd():
    with pytest.raises(ValidationError):
        UserPersona.model_validate({"name": "x", "description": "y", "primary_goal": "z", "extra": 1})


# ---------- Architecture ----------

def _sample_architecture() -> Architecture:
    return Architecture(
        tech_stack=TechStack(
            frontend="Next.js", backend="FastAPI", database="Postgres",
            auth="JWT", hosting="AWS", third_party_services=["Stripe"],
        ),
        system_design=[SystemComponent(name="API", responsibility="serve", component_type=ComponentType.BACKEND)],
        data_flows=[DataFlow(from_component="Web", to_component="API", description="user requests", method="REST")],
        database_schema=[DBTable(
            name="users",
            columns=[
                DBColumn(name="id", col_type="uuid", nullable=False, primary_key=True),
                DBColumn(name="email", col_type="text", nullable=False),
            ],
            indexes=["email"],
        )],
        api_contracts=[APIContract(
            contract_id="C1", endpoint="/users", method="GET", auth_required=True,
            description="list users", response_body={"users": []},
        )],
        folder_structure="root/\n  api/\n",
        environment_variables=[EnvironmentVariable(key="DB_URL", description="db", required=True)],
        architecture_decisions=[ArchitectureDecision(
            decision="Use Postgres", rationale="ACID", alternatives_considered=["Mongo"],
        )],
        risk_flags=[RiskFlag(risk="latency", mitigation="cache")],
    )


def test_architecture_round_trip():
    _round_trip(_sample_architecture())


# ---------- Task ----------

def _sample_task() -> Task:
    return Task(
        task_id="BE-001",
        title="Users API",
        description="Implement /users",
        agent_type=AgentType.BACKEND,
        depends_on=[],
        acceptance_criteria=["GET /users returns list"],
        files_to_create=["api/users.py"],
        api_contracts_used=["C1"],
        estimated_complexity=TaskComplexity.MEDIUM,
        context_package=ContextPackage(
            relevant_prd_sections=["FR-1"],
            relevant_api_contracts=[{"contract_id": "C1"}],
            relevant_schema_tables=["users"],
            relevant_folder_structure="api/",
        ),
    )


def test_task_round_trip():
    _round_trip(_sample_task())


def test_task_graph_round_trip():
    tg = TaskGraph(project_id="proj-1", tasks=[_sample_task()], execution_order=[["BE-001"]])
    _round_trip(tg)


def test_builder_output_round_trip():
    bo = BuilderOutput(
        task_id="BE-001",
        agent_type=AgentType.BACKEND,
        files_created=[{"path": "api/users.py", "line_count": 42}],
        tests_written=[{"path": "tests/test_users.py", "test_count": 5}],
        branch_name="agent/backend/BE-001",
        commit_sha="abc123",
        notes="ok",
        env_vars_required=["DB_URL"],
    )
    _round_trip(bo)


def test_task_status_enum():
    # Confirm enum members exist (used by orchestrator state machine)
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.DONE.value == "done"


# ---------- Validation reports ----------

def test_test_report_round_trip():
    tr = TestReport(
        task_id="BE-001",
        tests_added=[{"path": "tests/x.py", "test_names": ["test_a"]}],
        test_run_result=TestRunResult(
            passed=10, failed=1, skipped=0, coverage_percent=85.0,
            failures=[TestFailure(test_name="test_a", error_message="boom")],
        ),
        status="failed",
    )
    _round_trip(tr)


def test_security_quality_report_round_trip():
    sqr = SecurityQualityReport(
        task_id="BE-001",
        security_findings=[SecurityFinding(
            severity=Severity.HIGH, rule="S1", file="a.py", line=1,
            description="x", fix_suggestion="y",
        )],
        quality_findings=[QualityFinding(finding_type="complexity", file="a.py", line=2, description="too complex")],
        secret_scan={"secrets_found": False, "findings": []},
        scores={"security_score": 90, "quality_score": 80},
        status="passed",
        blocking_findings=[],
    )
    _round_trip(sqr)


# ---------- Delivery ----------

def test_review_summary_round_trip():
    rs = ReviewSummary(
        project_id="proj-1",
        overall_status="auto_approved",
        prd_coverage=PRDCoverage(
            requirements_covered=["FR-1"], requirements_missing=[], coverage_percent=100.0,
        ),
        gate_summary=GateSummary(
            tests_passed=True, security_passed=True, quality_passed=True, all_files_present=True,
        ),
        flags_for_human=[FlagForHuman(
            flag_type="coverage", description="d", options=["a", "b"], recommended_option="a",
        )],
        auto_approval_rationale="all gates green",
        merge_ready=True,
    )
    _round_trip(rs)


def test_delivery_package_round_trip():
    dp = DeliveryPackage(
        project_id="proj-1",
        project_name="Acme",
        documents_generated=DocumentsGenerated(
            api_docs="docs/api.md", setup_guide="SETUP.md", architecture_overview="ARCHITECTURE.md",
            runbook="RUNBOOK.md", env_template=".env.example",
        ),
        proof_report_path="delivery/proof.pdf",
        deployment_scripts=["deploy.sh"],
        delivery_package_path="delivery/package.zip",
    )
    _round_trip(dp)
