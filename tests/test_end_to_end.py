"""End-to-end Phase 6 test: Review + Docs/Delivery on a tiny todo-list project.

Mocks all Anthropic / Claude Code calls. Asserts that:
- Review Agent auto-approves a clean project
- Docs/Delivery Agent generates all five docs + a zip package
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from agents.docs_delivery_agent import DocsDeliveryAgent
from agents.review_agent import ReviewAgent
from schemas.architecture import (
    APIContract,
    Architecture,
    ArchitectureDecision,
    ComponentType,
    DataFlow,
    DBColumn,
    DBTable,
    EnvironmentVariable,
    SystemComponent,
    TechStack,
)
from schemas.prd import (
    PRD,
    FunctionalRequirement,
    NonFunctionalRequirement,
    Priority,
    UserPersona,
    UserStory,
)


def _todo_prd() -> dict:
    return PRD(
        project_name="Todo API",
        problem_statement="Users want a place to list and tick off tasks.",
        user_personas=[UserPersona(name="User", description="Anyone", primary_goal="Track tasks")],
        user_stories=[UserStory(
            story_id="US-1", as_a="user", i_want="add tasks",
            so_that="I remember them", acceptance_criteria=["POST /tasks creates a task"],
            priority=Priority.HIGH,
        )],
        functional_requirements=[
            FunctionalRequirement(req_id="FR-1", description="Create a task", priority=Priority.HIGH),
            FunctionalRequirement(req_id="FR-2", description="List tasks", priority=Priority.HIGH),
        ],
        non_functional_requirements=[
            NonFunctionalRequirement(category="performance", requirement="<200ms p95"),
        ],
        out_of_scope=["frontend"],
        success_metrics=["100 active users"],
        edge_cases=["empty task list"],
    ).model_dump(mode="json")


def _todo_architecture() -> dict:
    return Architecture(
        tech_stack=TechStack(
            frontend="None", backend="FastAPI", database="SQLite",
            auth="None", hosting="Self-hosted",
        ),
        system_design=[SystemComponent(name="API", responsibility="serve tasks", component_type=ComponentType.BACKEND)],
        data_flows=[DataFlow(from_component="Client", to_component="API", description="REST", method="REST")],
        database_schema=[DBTable(
            name="tasks",
            columns=[
                DBColumn(name="id", col_type="int", nullable=False, primary_key=True),
                DBColumn(name="title", col_type="text", nullable=False),
                DBColumn(name="done", col_type="bool", nullable=False),
            ],
        )],
        api_contracts=[
            APIContract(contract_id="C1", endpoint="/tasks", method="POST", auth_required=False,
                        description="Create a task", response_body={"id": 0}),
            APIContract(contract_id="C2", endpoint="/tasks", method="GET", auth_required=False,
                        description="List tasks", response_body={"tasks": []}),
        ],
        folder_structure="api/\n  main.py\n  models.py\n",
        environment_variables=[EnvironmentVariable(key="DB_URL", description="DB path", required=True)],
        architecture_decisions=[ArchitectureDecision(
            decision="Use FastAPI", rationale="Lightweight + great docs",
        )],
    ).model_dump(mode="json")


def _task_results() -> list[dict]:
    """Simulate completed tasks for FR-1 and FR-2."""
    return [
        {
            "task": {
                "task_id": "BE-001", "title": "Create task endpoint",
                "description": "Implement POST /tasks for FR-1",
                "acceptance_criteria": ["POST /tasks creates a task"],
                "context_package": {
                    "relevant_prd_sections": ["FR-1"],
                    "relevant_api_contracts": [], "relevant_schema_tables": ["tasks"],
                    "relevant_folder_structure": "api/",
                },
            },
            "builder_output": {"files_created": [{"path": "api/main.py", "line_count": 25}]},
            "test_result": {"status": "passed", "test_run_result": {"passed": 5, "failed": 0, "skipped": 0, "coverage_percent": 90.0, "failures": []}},
            "security_result": {
                "status": "passed", "scores": {"security_score": 95, "quality_score": 90},
                "blocking_findings": [],
            },
        },
        {
            "task": {
                "task_id": "BE-002", "title": "List tasks endpoint",
                "description": "Implement GET /tasks for FR-2",
                "acceptance_criteria": ["GET /tasks returns all tasks"],
                "context_package": {
                    "relevant_prd_sections": ["FR-2"],
                    "relevant_api_contracts": [], "relevant_schema_tables": ["tasks"],
                    "relevant_folder_structure": "api/",
                },
            },
            "builder_output": {"files_created": [{"path": "api/models.py", "line_count": 15}]},
            "test_result": {"status": "passed", "test_run_result": {"passed": 3, "failed": 0, "skipped": 0, "coverage_percent": 95.0, "failures": []}},
            "security_result": {
                "status": "passed", "scores": {"security_score": 100, "quality_score": 95},
                "blocking_findings": [],
            },
        },
    ]


# ---------------- Review Agent ----------------

async def test_review_agent_auto_approves_clean_project():
    agent = ReviewAgent()
    result = await agent.run({
        "project_id": "proj-todo",
        "prd": _todo_prd(),
        "architecture": _todo_architecture(),
        "task_results": _task_results(),
        "all_files": ["api/main.py", "api/models.py"],
    })
    assert result.status == "success"
    assert result.payload["overall_status"] == "auto_approved"
    assert result.payload["merge_ready"] is True
    assert result.payload["prd_coverage"]["coverage_percent"] == 100.0
    assert result.payload["flags_for_human"] == []


async def test_review_agent_flags_when_security_blocker():
    trs = _task_results()
    trs[0]["security_result"] = {
        "status": "failed",
        "scores": {"security_score": 40, "quality_score": 80},
        "blocking_findings": [{
            "severity": "critical", "rule": "X", "file": "y", "line": 1,
            "description": "d", "fix_suggestion": "f",
        }],
    }
    agent = ReviewAgent()
    result = await agent.run({
        "project_id": "proj-todo",
        "prd": _todo_prd(),
        "architecture": _todo_architecture(),
        "task_results": trs,
        "all_files": ["api/main.py", "api/models.py"],
    })
    assert result.status == "needs_human"
    assert any(f["flag_type"] == "security_blocker" for f in result.payload["flags_for_human"])


async def test_review_agent_flags_low_prd_coverage():
    trs = _task_results()
    # Strip one task so FR-2 is not covered
    trs = [trs[0]]
    agent = ReviewAgent()
    result = await agent.run({
        "project_id": "proj-todo",
        "prd": _todo_prd(),
        "architecture": _todo_architecture(),
        "task_results": trs,
        "all_files": ["api/main.py"],
    })
    assert result.status == "needs_human"
    assert result.payload["prd_coverage"]["coverage_percent"] == 50.0
    assert "FR-2" in result.payload["prd_coverage"]["requirements_missing"]


# ---------------- Docs & Delivery ----------------

async def test_docs_delivery_generates_all_artifacts(tmp_path):
    agent = DocsDeliveryAgent(projects_dir=tmp_path, skip_pdf=True)
    result = await agent.run({
        "project_id": "proj-todo",
        "project_name": "Todo API",
        "client_name": "Acme",
        "prd": _todo_prd(),
        "architecture": _todo_architecture(),
        "all_task_results": _task_results(),
        "all_validation_reports": [
            {"type": "test", "test_run_result": {"passed": 8, "failed": 0}},
            {"type": "security", "status": "passed"},
            {"type": "security", "status": "passed"},
        ],
        "repo_url": "https://github.com/acme/todo",
    })
    assert result.status == "success"

    payload = result.payload
    docs = payload["documents_generated"]
    for key in ("api_docs", "setup_guide", "architecture_overview", "runbook", "env_template"):
        assert Path(docs[key]).exists(), f"missing: {key}"

    # Zip package
    pkg = Path(payload["delivery_package_path"])
    assert pkg.exists()
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
    for expected in {"api_docs.md", "SETUP.md", "ARCHITECTURE.md", "RUNBOOK.md", ".env.example"}:
        assert expected in names

    # Spot-check content of generated docs.
    api_docs_text = Path(docs["api_docs"]).read_text()
    assert "/tasks" in api_docs_text
    assert "POST" in api_docs_text

    setup_text = Path(docs["setup_guide"]).read_text()
    assert "DB_URL" in setup_text
    assert "FastAPI" in setup_text

    arch_text = Path(docs["architecture_overview"]).read_text()
    assert "FastAPI" in arch_text
    assert "tasks" in arch_text

    env_text = Path(docs["env_template"]).read_text()
    assert "DB_URL=" in env_text
