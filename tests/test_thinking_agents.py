"""Tests for thinking agents with a mocked Anthropic client."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from agents._anthropic_client import LLMResponse, extract_json
from agents.architect_agent import ArchitectAgent
from agents.clarification_agent import ClarificationAgent
from agents.task_planner_agent import TaskPlannerAgent
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


class MockClient:
    """Records calls and returns canned responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        text = self._responses.pop(0)
        return LLMResponse(text=text)


# ---------------- extract_json helper ----------------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_in_fence():
    text = 'Some prose.\n```json\n{"a": 2}\n```\nMore prose.'
    assert extract_json(text) == {"a": 2}


def test_extract_json_with_prose_no_fence():
    text = 'Here is the JSON: {"a": 3, "b": [1,2]} thanks!'
    assert extract_json(text) == {"a": 3, "b": [1, 2]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("absolutely no json here")


# ---------------- Clarification Agent ----------------

def _sample_prd_dict() -> dict:
    return PRD(
        project_name="Acme Portal",
        problem_statement="Track orders",
        user_personas=[UserPersona(name="Op", description="d", primary_goal="g")],
        user_stories=[UserStory(
            story_id="US-1", as_a="op", i_want="dashboard", so_that="track",
            acceptance_criteria=["loads"], priority=Priority.HIGH,
        )],
        functional_requirements=[FunctionalRequirement(
            req_id="FR-1", description="show", priority=Priority.HIGH,
        )],
        non_functional_requirements=[NonFunctionalRequirement(category="perf", requirement="<500ms")],
        out_of_scope=[],
        success_metrics=["adoption"],
        edge_cases=["empty list"],
    ).model_dump(mode="json")


async def test_clarification_agent_returns_prd_when_ready(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    response = json.dumps({
        "status": "prd_ready",
        "clarification_questions": [],
        "prd": _sample_prd_dict(),
    })
    client = MockClient([response])
    agent = ClarificationAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "raw_brief": "Build an order portal",
        "client_name": "Acme",
        "budget_range": "$10k",
        "timeline_weeks": 4,
    })
    assert result.status == "success"
    assert result.payload["status"] == "prd_ready"
    assert result.payload["prd"]["project_name"] == "Acme Portal"
    assert len(client.calls) == 1


async def test_clarification_agent_asks_questions(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    response = json.dumps({
        "status": "needs_clarification",
        "clarification_questions": ["Who are the users?", "What stack?"],
        "prd": None,
    })
    client = MockClient([response])
    agent = ClarificationAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "raw_brief": "build a thing",
        "client_name": "X",
        "budget_range": "$1k",
        "timeline_weeks": 1,
    })
    # Has questions to ask → agent succeeds and returns them.
    assert result.status == "success"
    assert result.payload["clarification_questions"] == ["Who are the users?", "What stack?"]


async def test_clarification_agent_reprompts_on_invalid_json(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    bad = "this is not json"
    good = json.dumps({"status": "needs_clarification", "clarification_questions": ["?"], "prd": None})
    client = MockClient([bad, good])
    agent = ClarificationAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "raw_brief": "x", "client_name": "c", "budget_range": "$1", "timeline_weeks": 1,
    })
    assert result.status == "success"
    assert len(client.calls) == 2
    # Second call should have the validation error injected.
    assert "validation" in client.calls[1]["user"].lower() or "parse" in client.calls[1]["user"].lower()


async def test_clarification_agent_passes_previous_answers(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    response = json.dumps({"status": "needs_clarification", "clarification_questions": ["More?"], "prd": None})
    client = MockClient([response])
    agent = ClarificationAgent(client=client, prompt_path=prompt_file)
    prior = [{"question": "Stack?", "answer": "Python"}]
    await agent.run({
        "raw_brief": "x", "client_name": "c", "budget_range": "$1",
        "timeline_weeks": 1, "previous_answers": prior,
    })
    assert "Python" in client.calls[0]["user"]


# ---------------- Architect Agent ----------------

def _sample_arch_dict() -> dict:
    return Architecture(
        tech_stack=TechStack(frontend="Next.js", backend="FastAPI", database="Postgres",
                             auth="JWT", hosting="AWS"),
        system_design=[SystemComponent(name="API", responsibility="r", component_type=ComponentType.BACKEND)],
        data_flows=[DataFlow(from_component="Web", to_component="API", description="d", method="REST")],
        database_schema=[DBTable(
            name="users",
            columns=[DBColumn(name="id", col_type="uuid", nullable=False, primary_key=True)],
        )],
        api_contracts=[APIContract(
            contract_id="C1", endpoint="/users", method="GET", auth_required=True,
            description="d", response_body={"users": []},
        )],
        folder_structure="root/\n",
        environment_variables=[EnvironmentVariable(key="DB_URL", description="d", required=True)],
        architecture_decisions=[ArchitectureDecision(decision="d", rationale="r")],
    ).model_dump(mode="json")


async def test_architect_agent_returns_architecture(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    client = MockClient([json.dumps(_sample_arch_dict())])
    agent = ArchitectAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "prd": _sample_prd_dict(),
        "budget_usd": 15000,
        "timeline_weeks": 6,
        "deployment_target": "aws",
    })
    assert result.status == "success"
    assert result.payload["tech_stack"]["backend"] == "FastAPI"


async def test_architect_agent_reprompts_on_invalid_schema(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    bad = json.dumps({"tech_stack": {}})  # missing required fields
    good = json.dumps(_sample_arch_dict())
    client = MockClient([bad, good])
    agent = ArchitectAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "prd": _sample_prd_dict(),
        "budget_usd": 15000,
        "timeline_weeks": 6,
        "deployment_target": "aws",
    })
    assert result.status == "success"
    assert len(client.calls) == 2


# ---------------- Task Planner ----------------

def _sample_taskgraph_dict(file_a: str = "api/users.py", file_b: str = "api/orders.py") -> dict:
    return {
        "project_id": "proj-1",
        "tasks": [
            {
                "task_id": "BE-001", "title": "users API", "description": "d",
                "agent_type": "backend", "depends_on": [],
                "acceptance_criteria": ["impl"], "files_to_create": [file_a],
                "files_to_modify": [], "api_contracts_used": [],
                "estimated_complexity": "low",
                "context_package": {
                    "relevant_prd_sections": [], "relevant_api_contracts": [],
                    "relevant_schema_tables": [], "relevant_folder_structure": "",
                },
            },
            {
                "task_id": "BE-002", "title": "orders API", "description": "d",
                "agent_type": "backend", "depends_on": [],
                "acceptance_criteria": ["impl"], "files_to_create": [file_b],
                "files_to_modify": [], "api_contracts_used": [],
                "estimated_complexity": "low",
                "context_package": {
                    "relevant_prd_sections": [], "relevant_api_contracts": [],
                    "relevant_schema_tables": [], "relevant_folder_structure": "",
                },
            },
        ],
        "execution_order": [["BE-001", "BE-002"]],
    }


async def test_task_planner_returns_task_graph(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    client = MockClient([json.dumps(_sample_taskgraph_dict())])
    agent = TaskPlannerAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "architecture": _sample_arch_dict(),
        "prd": _sample_prd_dict(),
        "project_id": "proj-1",
    })
    assert result.status == "success"
    assert len(result.payload["tasks"]) == 2


async def test_task_planner_detects_file_overlap(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("system prompt")
    # First response: both tasks share api/users.py in the same batch → overlap.
    overlap = _sample_taskgraph_dict(file_a="api/users.py", file_b="api/users.py")
    # Second response: separated files → ok.
    ok = _sample_taskgraph_dict()
    client = MockClient([json.dumps(overlap), json.dumps(ok)])
    agent = TaskPlannerAgent(client=client, prompt_path=prompt_file)
    result = await agent.run({
        "architecture": _sample_arch_dict(),
        "prd": _sample_prd_dict(),
        "project_id": "proj-1",
    })
    assert result.status == "success"
    assert len(client.calls) == 2
    assert "overlap" in client.calls[1]["user"]
