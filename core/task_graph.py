"""TaskGraph runtime wrapper around schemas.task.TaskGraph.

Tracks per-task status in memory and exposes:
- get_ready_tasks() — tasks whose dependencies are all DONE
- mark_done / mark_failed / mark_running
- is_complete()
- get_batches() — execution_order from the schema
"""
from __future__ import annotations

from typing import Optional

from schemas.task import Task, TaskGraph as TaskGraphSchema, TaskStatus


class TaskGraph:
    """In-memory task graph with status tracking."""

    def __init__(self, graph: TaskGraphSchema):
        self.schema = graph
        self.project_id = graph.project_id
        self._tasks: dict[str, Task] = {t.task_id: t for t in graph.tasks}
        self._status: dict[str, TaskStatus] = {
            t.task_id: TaskStatus.PENDING for t in graph.tasks
        }

    @property
    def tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        return self._status.get(task_id)

    def get_ready_tasks(self) -> list[Task]:
        """Tasks that are PENDING and whose dependencies are all DONE."""
        ready: list[Task] = []
        for tid, task in self._tasks.items():
            if self._status[tid] != TaskStatus.PENDING:
                continue
            if all(
                self._status.get(dep) == TaskStatus.DONE for dep in task.depends_on
            ):
                ready.append(task)
        return ready

    def mark_running(self, task_id: str) -> None:
        self._ensure(task_id)
        self._status[task_id] = TaskStatus.RUNNING

    def mark_validating(self, task_id: str) -> None:
        self._ensure(task_id)
        self._status[task_id] = TaskStatus.VALIDATING

    def mark_done(self, task_id: str) -> None:
        self._ensure(task_id)
        self._status[task_id] = TaskStatus.DONE

    def mark_failed(self, task_id: str) -> None:
        self._ensure(task_id)
        self._status[task_id] = TaskStatus.FAILED

    def mark_escalated(self, task_id: str) -> None:
        self._ensure(task_id)
        self._status[task_id] = TaskStatus.ESCALATED

    def is_complete(self) -> bool:
        """All tasks are DONE."""
        return all(s == TaskStatus.DONE for s in self._status.values())

    def has_failures(self) -> bool:
        return any(
            s in (TaskStatus.FAILED, TaskStatus.ESCALATED)
            for s in self._status.values()
        )

    def get_batches(self) -> list[list[Task]]:
        """Resolve execution_order (batches of task_ids) into batches of Tasks."""
        return [
            [self._tasks[tid] for tid in batch if tid in self._tasks]
            for batch in self.schema.execution_order
        ]

    def status_snapshot(self) -> dict[str, str]:
        return {tid: s.value for tid, s in self._status.items()}

    def _ensure(self, task_id: str) -> None:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task_id: {task_id}")
