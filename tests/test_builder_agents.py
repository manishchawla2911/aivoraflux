"""Tests for builder agents: CLAUDE.md generation, subprocess parsing, git ops."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.backend_agent import BackendAgent
from agents.devops_agent import DevOpsAgent
from agents.frontend_agent import FrontendAgent
from agents.integration_agent import IntegrationAgent
from core.git_manager import GitManager
from schemas.task import AgentType, ContextPackage, Task, TaskComplexity


def _ctx(api_contracts=None, tables=None) -> ContextPackage:
    return ContextPackage(
        relevant_prd_sections=["FR-1"],
        relevant_api_contracts=api_contracts or [],
        relevant_schema_tables=tables or [],
        relevant_folder_structure="api/\n  users.py\n",
    )


def _task(task_id: str, agent_type: AgentType, files=None) -> Task:
    return Task(
        task_id=task_id,
        title=f"Build {task_id}",
        description="d",
        agent_type=agent_type,
        depends_on=[],
        acceptance_criteria=["GET works", "POST works"],
        files_to_create=files or [f"src/{task_id}.py"],
        estimated_complexity=TaskComplexity.MEDIUM,
        context_package=_ctx(
            api_contracts=[{"contract_id": "C1", "endpoint": "/users", "method": "GET"}],
            tables=["users"],
        ),
    )


def _mock_runner(summary: dict):
    def runner(claude_md_path: Path, repo_path: Path, timeout: int) -> str:
        return f"Done!\n```json\n{json.dumps(summary)}\n```"
    return runner


@pytest.fixture()
def repo(tmp_path):
    repo_path = tmp_path / "workrepo"
    repo_path.mkdir()
    return repo_path


@pytest.fixture()
def git(repo):
    # Pre-init the repo so commits work
    from git import Repo
    r = Repo.init(str(repo))
    # Need a baseline commit so we can branch.
    (repo / "README.md").write_text("init")
    r.index.add(["README.md"])
    r.index.commit("init")
    return GitManager(repo_path=repo)


def _write_files(repo_path: Path, files: list[str]):
    for f in files:
        p = repo_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n")


# ---------------- Backend ----------------

async def test_backend_agent_renders_claude_md_and_returns_builder_output(repo, git, tmp_path):
    task = _task("BE-001", AgentType.BACKEND)
    summary = {
        "files_created": [{"path": "src/BE-001.py", "line_count": 10}],
        "tests_written": [{"path": "tests/test_BE-001.py", "test_count": 3}],
        "notes": "done",
    }
    _write_files(repo, ["src/BE-001.py"])

    agent = BackendAgent(
        repo_path=repo, git_manager=git, claude_code_runner=_mock_runner(summary),
    )
    monkey_projects = tmp_path / "projects"
    import agents._builder_base as bb
    bb.PROJECTS_DIR = monkey_projects

    result = await agent.run({
        "task": task.model_dump(mode="json"),
        "project_id": "proj-1",
        "backend_stack": "FastAPI",
        "database": "Postgres",
    })

    assert result.status == "success"
    assert result.payload["agent_type"] == "backend"
    assert result.payload["task_id"] == "BE-001"
    assert result.payload["branch_name"] == "agent/backend/BE-001"
    # CLAUDE.md should have been written for this task
    claude_md = monkey_projects / "proj-1" / "CLAUDE.BE-001.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "BE-001" in content
    assert "FastAPI" in content
    assert "Postgres" in content
    # Numbered acceptance criteria
    assert "1. GET works" in content
    # API contracts injected as JSON
    assert "/users" in content


async def test_backend_agent_handles_bad_json_from_claude_code(repo, git, tmp_path):
    task = _task("BE-002", AgentType.BACKEND)
    bad_runner = lambda *a, **k: "no json here at all"
    agent = BackendAgent(repo_path=repo, git_manager=git, claude_code_runner=bad_runner)
    import agents._builder_base as bb
    bb.PROJECTS_DIR = tmp_path / "projects"
    result = await agent.run({"task": task.model_dump(mode="json"), "project_id": "proj-1"})
    assert result.status == "failed"
    assert "summary" in (result.error or "")


# ---------------- Frontend ----------------

async def test_frontend_agent_branch_pattern(repo, git, tmp_path):
    task = _task("FE-001", AgentType.FRONTEND)
    summary = {"files_created": [{"path": "src/FE-001.tsx", "line_count": 5}],
               "tests_written": [], "notes": ""}
    _write_files(repo, ["src/FE-001.tsx"])
    agent = FrontendAgent(
        repo_path=repo, git_manager=git, claude_code_runner=_mock_runner(summary),
    )
    import agents._builder_base as bb
    bb.PROJECTS_DIR = tmp_path / "projects"
    result = await agent.run({
        "task": task.model_dump(mode="json"), "project_id": "proj-1",
        "frontend_stack": "Next.js",
    })
    assert result.status == "success"
    assert result.payload["branch_name"] == "agent/frontend/FE-001"
    assert result.payload["agent_type"] == "frontend"
    content = (tmp_path / "projects" / "proj-1" / "CLAUDE.FE-001.md").read_text()
    assert "Next.js" in content


# ---------------- Integration ----------------

async def test_integration_agent_captures_env_vars(repo, git, tmp_path):
    task = _task("INT-001", AgentType.INTEGRATION)
    summary = {
        "files_created": [{"path": "src/INT-001.py", "line_count": 12}],
        "tests_written": [],
        "env_vars_required": ["STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET"],
        "notes": "uses Stripe SDK",
    }
    _write_files(repo, ["src/INT-001.py"])
    agent = IntegrationAgent(
        repo_path=repo, git_manager=git, claude_code_runner=_mock_runner(summary),
    )
    import agents._builder_base as bb
    bb.PROJECTS_DIR = tmp_path / "projects"
    result = await agent.run({
        "task": task.model_dump(mode="json"), "project_id": "proj-1",
        "service_name": "Stripe", "integration_type": "payment",
    })
    assert result.status == "success"
    assert result.payload["branch_name"] == "agent/integration/INT-001"
    assert result.payload["env_vars_required"] == ["STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET"]
    content = (tmp_path / "projects" / "proj-1" / "CLAUDE.INT-001.md").read_text()
    assert "Stripe" in content


# ---------------- DevOps ----------------

async def test_devops_agent_renders_tech_stack(repo, git, tmp_path):
    task = _task("OPS-001", AgentType.DEVOPS, files=["Dockerfile", ".github/workflows/ci.yml"])
    summary = {
        "files_created": [
            {"path": "Dockerfile", "line_count": 15},
            {"path": ".github/workflows/ci.yml", "line_count": 30},
        ],
        "tests_written": [],
        "notes": "",
    }
    _write_files(repo, ["Dockerfile", ".github/workflows/ci.yml"])
    agent = DevOpsAgent(
        repo_path=repo, git_manager=git, claude_code_runner=_mock_runner(summary),
    )
    import agents._builder_base as bb
    bb.PROJECTS_DIR = tmp_path / "projects"
    result = await agent.run({
        "task": task.model_dump(mode="json"), "project_id": "proj-1",
        "tech_stack": {"frontend": "Next.js", "backend": "FastAPI"},
        "deployment_target": "aws",
        "environment": "production",
    })
    assert result.status == "success"
    assert result.payload["branch_name"] == "agent/devops/OPS-001"
    content = (tmp_path / "projects" / "proj-1" / "CLAUDE.OPS-001.md").read_text()
    assert "aws" in content
    assert "production" in content
    assert "FastAPI" in content


# ---------------- Git operations ----------------

def test_git_manager_creates_branch_and_commits(repo):
    from git import Repo
    r = Repo.init(str(repo))
    (repo / "x.txt").write_text("init")
    r.index.add(["x.txt"])
    r.index.commit("init")
    g = GitManager(repo_path=repo)
    g.create_branch("agent/backend/BE-001")
    (repo / "new.py").write_text("print('hi')")
    sha = g.commit_files(["new.py"], "BE-001: add file")
    assert isinstance(sha, str)
    assert len(sha) >= 7
    # We should be on the new branch
    assert r.active_branch.name == "agent/backend/BE-001"
