"""Tests for core.task_graph.TaskGraph runtime wrapper."""
from __future__ import annotations

import pytest

from core.task_graph import TaskGraph
from schemas.task import (
    AgentType,
    ContextPackage,
    Task,
    TaskComplexity,
    TaskGraph as TaskGraphSchema,
)


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


def _graph(*tasks: Task, batches=None) -> TaskGraph:
    schema = TaskGraphSchema(
        project_id="proj-1",
        tasks=list(tasks),
        execution_order=batches or [[t.task_id for t in tasks]],
    )
    return TaskGraph(schema)


def test_initial_ready_tasks_have_no_deps():
    g = _graph(
        _task("BE-001"),
        _task("BE-002", depends_on=["BE-001"]),
        _task("OPS-001", agent_type=AgentType.DEVOPS),
    )
    ready_ids = {t.task_id for t in g.get_ready_tasks()}
    assert ready_ids == {"BE-001", "OPS-001"}


def test_dependency_resolution_unblocks_downstream():
    g = _graph(
        _task("BE-001"),
        _task("BE-002", depends_on=["BE-001"]),
    )
    g.mark_running("BE-001")
    assert {t.task_id for t in g.get_ready_tasks()} == set()
    g.mark_done("BE-001")
    assert {t.task_id for t in g.get_ready_tasks()} == {"BE-002"}


def test_is_complete_true_only_when_all_done():
    g = _graph(_task("BE-001"), _task("BE-002", depends_on=["BE-001"]))
    assert not g.is_complete()
    g.mark_done("BE-001")
    assert not g.is_complete()
    g.mark_done("BE-002")
    assert g.is_complete()


def test_failed_does_not_complete_graph():
    g = _graph(_task("BE-001"))
    g.mark_failed("BE-001")
    assert not g.is_complete()
    assert g.has_failures()


def test_get_batches_returns_resolved_tasks():
    g = _graph(
        _task("BE-001"),
        _task("OPS-001", agent_type=AgentType.DEVOPS),
        _task("BE-002", depends_on=["BE-001"]),
        batches=[["BE-001", "OPS-001"], ["BE-002"]],
    )
    batches = g.get_batches()
    assert len(batches) == 2
    assert {t.task_id for t in batches[0]} == {"BE-001", "OPS-001"}
    assert {t.task_id for t in batches[1]} == {"BE-002"}


def test_unknown_task_id_raises():
    g = _graph(_task("BE-001"))
    with pytest.raises(KeyError):
        g.mark_done("DOES-NOT-EXIST")
