"""Tests for orchestrator state transitions and dispatch with mock agents."""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session

from agents.base_agent import AgentResult, BaseAgent
from core.event_bus import EventBus
from core.notifier import SlackNotifier
from core.orchestrator import Orchestrator, VALID_TRANSITIONS
from core.state import Project as ProjectRow, get_engine, init_db
from core.task_graph import TaskGraph
from schemas.task import (
    AgentType,
    ContextPackage,
    Task,
    TaskComplexity,
    TaskGraph as TaskGraphSchema,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'orchestrator.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    yield


def _ctx() -> ContextPackage:
    return ContextPackage(
        relevant_prd_sections=[],
        relevant_api_contracts=[],
        relevant_schema_tables=[],
        relevant_folder_structure="",
    )


def _task(task_id: str, agent_type: AgentType = AgentType.BACKEND, depends_on=None) -> Task:
    return Task(
        task_id=task_id,
        title=task_id,
        description="d",
        agent_type=agent_type,
        depends_on=depends_on or [],
        acceptance_criteria=["ok"],
        files_to_create=[f"{task_id}.py"],
        estimated_complexity=TaskComplexity.LOW,
        context_package=_ctx(),
    )


class _MockAgent(BaseAgent):
    name = "mock"
    model = "test"

    def __init__(self, status: str = "success", payload=None):
        super().__init__()
        self._status = status
        self._payload = payload or {"ok": True}
        self.calls: list[dict] = []

    async def run(self, input: dict) -> AgentResult:
        self.calls.append(input)
        return AgentResult(status=self._status, payload=self._payload)


def _create_project(project_id: str, status: str = "CREATED") -> None:
    with Session(get_engine()) as s:
        s.add(ProjectRow(id=project_id, name=project_id, status=status))
        s.commit()


# ---------------- state machine ----------------

def test_valid_transitions_table_complete():
    states = set(VALID_TRANSITIONS.keys())
    assert "CREATED" in states
    assert "DELIVERED" in states
    # DELIVERED is terminal
    assert VALID_TRANSITIONS["DELIVERED"] == set()


def test_transition_happy_path(db):
    _create_project("proj-1", status="CREATED")
    o = Orchestrator()
    o.transition("proj-1", "CLARIFYING")
    o.transition("proj-1", "AWAITING_PRD_APPROVAL")
    o.transition("proj-1", "ARCHITECTING")
    with Session(get_engine()) as s:
        assert s.get(ProjectRow, "proj-1").status == "ARCHITECTING"


def test_transition_rejects_illegal(db):
    _create_project("proj-2", status="CREATED")
    o = Orchestrator()
    with pytest.raises(ValueError):
        o.transition("proj-2", "DELIVERED")


def test_transition_unknown_project(db):
    o = Orchestrator()
    with pytest.raises(ValueError):
        o.transition("does-not-exist", "CLARIFYING")


# ---------------- dispatch + parallelism ----------------

@pytest.mark.asyncio
async def test_run_project_dispatches_all_tasks(db):
    _create_project("proj-3", status="PLANNING")
    schema = TaskGraphSchema(
        project_id="proj-3",
        tasks=[_task("BE-001"), _task("BE-002", depends_on=["BE-001"])],
        execution_order=[["BE-001"], ["BE-002"]],
    )
    graph = TaskGraph(schema)
    agent = _MockAgent(status="success", payload={"files_created": []})
    o = Orchestrator(
        event_bus=EventBus(),
        notifier=SlackNotifier(),
        agents={"backend": agent},
        max_parallel_agents=2,
        poll_interval_seconds=0.01,
    )
    o.attach_task_graph("proj-3", graph)
    await asyncio.wait_for(o.run_project("proj-3"), timeout=5)
    assert graph.is_complete()
    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_run_project_handles_missing_agent(db):
    _create_project("proj-4", status="PLANNING")
    schema = TaskGraphSchema(
        project_id="proj-4",
        tasks=[_task("BE-001")],
        execution_order=[["BE-001"]],
    )
    graph = TaskGraph(schema)
    o = Orchestrator(
        agents={},  # no backend agent registered
        max_parallel_agents=1,
        poll_interval_seconds=0.01,
    )
    o.attach_task_graph("proj-4", graph)
    await asyncio.wait_for(o.run_project("proj-4"), timeout=5)
    assert graph.has_failures()
    assert not graph.is_complete()


@pytest.mark.asyncio
async def test_event_bus_publishes_completed(db):
    _create_project("proj-5", status="PLANNING")
    received: list[dict] = []
    bus = EventBus()
    bus.subscribe("agent.completed", lambda d: received.append(d))

    schema = TaskGraphSchema(
        project_id="proj-5",
        tasks=[_task("BE-001")],
        execution_order=[["BE-001"]],
    )
    graph = TaskGraph(schema)
    o = Orchestrator(
        event_bus=bus,
        agents={"backend": _MockAgent(status="success")},
        max_parallel_agents=1,
        poll_interval_seconds=0.01,
    )
    o.attach_task_graph("proj-5", graph)
    await asyncio.wait_for(o.run_project("proj-5"), timeout=5)
    assert any(d.get("task_id") == "BE-001" for d in received)


# ---------------- base agent retry ----------------

class _FlakyAgent(BaseAgent):
    name = "flaky"
    model = "test"

    def __init__(self, fail_n: int):
        super().__init__(max_retries=3)
        self.fail_n = fail_n
        self.attempts = 0

    async def run(self, input: dict) -> AgentResult:
        self.attempts += 1
        if self.attempts <= self.fail_n:
            return AgentResult(status="failed", error="flaky")
        return AgentResult(status="success", payload={"ok": True})


@pytest.mark.asyncio
async def test_base_agent_retries_on_failure(monkeypatch):
    # Patch sleep to skip backoff in tests
    async def _no_sleep(_):
        pass
    monkeypatch.setattr("agents.base_agent.asyncio.sleep", _no_sleep)

    a = _FlakyAgent(fail_n=2)
    res = await a.run_with_retry({})
    assert res.status == "success"
    assert a.attempts == 3


@pytest.mark.asyncio
async def test_base_agent_gives_up_after_max_retries(monkeypatch):
    async def _no_sleep(_):
        pass
    monkeypatch.setattr("agents.base_agent.asyncio.sleep", _no_sleep)

    a = _FlakyAgent(fail_n=999)
    res = await a.run_with_retry({})
    assert res.status == "failed"
    assert "Max retries" in (res.error or "")
    assert a.attempts == 3


# ---------------- event bus ----------------

@pytest.mark.asyncio
async def test_event_bus_handles_sync_and_async():
    bus = EventBus()
    sync_calls: list[dict] = []
    async_calls: list[dict] = []

    async def async_handler(data):
        async_calls.append(data)

    bus.subscribe("task.ready", lambda d: sync_calls.append(d))
    bus.subscribe("task.ready", async_handler)
    await bus.publish("task.ready", {"task_id": "x"})
    assert sync_calls == [{"task_id": "x"}]
    assert async_calls == [{"task_id": "x"}]


@pytest.mark.asyncio
async def test_event_bus_unsubscribe():
    bus = EventBus()
    calls = []
    h = lambda d: calls.append(d)
    bus.subscribe("task.ready", h)
    bus.unsubscribe("task.ready", h)
    await bus.publish("task.ready", {})
    assert calls == []
