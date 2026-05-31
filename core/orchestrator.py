"""Orchestrator — runtime engine that moves work through the pipeline.

Responsibilities (per ORCHESTRATOR_SPEC.md section 1):
- Polls task graph for ready tasks and dispatches builder agents.
- Drives the project state machine (CREATED → ... → DELIVERED).
- Subscribes to event bus events and routes results.
- Does NOT make decisions; flags for humans via the notifier.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from sqlmodel import Session

from agents.base_agent import AgentResult, BaseAgent
from core.event_bus import EventBus
from core.notifier import SlackNotifier
from core.observability import record_event
from core.state import Project as ProjectRow, Task as TaskRow, get_engine, init_db, utcnow
from core.task_graph import TaskGraph
from schemas.task import AgentType, Task as TaskSchema

logger = logging.getLogger(__name__)


# State machine transitions (per ORCHESTRATOR_SPEC.md section 2)
PROJECT_STATES = [
    "CREATED",
    "CLARIFYING",
    "AWAITING_PRD_APPROVAL",
    "ARCHITECTING",
    "AWAITING_ARCH_APPROVAL",
    "PLANNING",
    "BUILDING",
    "VALIDATING",
    "REVIEWING",
    "AWAITING_REVIEW_APPROVAL",
    "GENERATING_DOCS",
    "AWAITING_DELIVERY_SIGNOFF",
    "DELIVERED",
    "FAILED",
    "PAUSED",
]

VALID_TRANSITIONS = {
    "CREATED": {"CLARIFYING", "FAILED", "PAUSED"},
    "CLARIFYING": {"AWAITING_PRD_APPROVAL", "FAILED", "PAUSED"},
    "AWAITING_PRD_APPROVAL": {"ARCHITECTING", "CLARIFYING", "FAILED", "PAUSED"},
    "ARCHITECTING": {"AWAITING_ARCH_APPROVAL", "FAILED", "PAUSED"},
    "AWAITING_ARCH_APPROVAL": {"PLANNING", "ARCHITECTING", "FAILED", "PAUSED"},
    "PLANNING": {"BUILDING", "FAILED", "PAUSED"},
    "BUILDING": {"VALIDATING", "FAILED", "PAUSED"},
    "VALIDATING": {"REVIEWING", "BUILDING", "FAILED", "PAUSED"},
    "REVIEWING": {"AWAITING_REVIEW_APPROVAL", "GENERATING_DOCS", "FAILED", "PAUSED"},
    "AWAITING_REVIEW_APPROVAL": {"GENERATING_DOCS", "BUILDING", "FAILED", "PAUSED"},
    "GENERATING_DOCS": {"AWAITING_DELIVERY_SIGNOFF", "FAILED", "PAUSED"},
    "AWAITING_DELIVERY_SIGNOFF": {"DELIVERED", "FAILED", "PAUSED"},
    "DELIVERED": set(),
    "FAILED": {"PAUSED"},
    "PAUSED": set(PROJECT_STATES),  # PAUSED can resume to anything
}


class Orchestrator:
    """The runtime engine. One instance manages many projects."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        notifier: Optional[SlackNotifier] = None,
        agents: Optional[dict[str, BaseAgent]] = None,
        max_parallel_agents: Optional[int] = None,
        poll_interval_seconds: float = 1.0,
    ):
        self.event_bus = event_bus or EventBus()
        self.notifier = notifier or SlackNotifier()
        # Map of agent key (e.g. "backend", "frontend", "architect") → agent instance.
        self.agents: dict[str, BaseAgent] = agents or {}
        self.max_parallel_agents = max_parallel_agents or int(
            os.getenv("MAX_PARALLEL_AGENTS", "4")
        )
        self.poll_interval_seconds = poll_interval_seconds
        self._task_graphs: dict[str, TaskGraph] = {}
        self._semaphore = asyncio.Semaphore(self.max_parallel_agents)
        self._running = False

        # Wire up default event handlers.
        self.event_bus.subscribe("agent.completed", self._on_agent_completed)
        self.event_bus.subscribe("agent.failed", self._on_agent_failed)
        self.event_bus.subscribe("agent.flag_human", self._on_flag_human)
        self.event_bus.subscribe("validation.complete", self._on_validation_complete)
        self.event_bus.subscribe("project.phase_done", self._on_phase_done)
        self.event_bus.subscribe("observability.anomaly", self._on_anomaly)

    # ----------------------------- public API -----------------------------

    def register_agent(self, key: str, agent: BaseAgent) -> None:
        self.agents[key] = agent

    def load_project(self, project_id: str) -> ProjectRow:
        """Load a project row from the DB. Raises if not found."""
        with Session(get_engine()) as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise ValueError(f"Project {project_id!r} not found")
            return row

    def attach_task_graph(self, project_id: str, graph: TaskGraph) -> None:
        self._task_graphs[project_id] = graph

    def get_task_graph(self, project_id: str) -> Optional[TaskGraph]:
        return self._task_graphs.get(project_id)

    def transition(self, project_id: str, new_state: str) -> None:
        """Transition a project to new_state if valid."""
        with Session(get_engine()) as session:
            row = session.get(ProjectRow, project_id)
            if row is None:
                raise ValueError(f"Project {project_id!r} not found")
            current = row.status
            if new_state not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"Invalid transition {current} → {new_state} for project {project_id}"
                )
            row.status = new_state
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
            logger.info(
                "orchestrator.transition",
                extra={"project_id": project_id, "from": current, "to": new_state},
            )

    async def run_project(self, project_id: str) -> None:
        """Run the build phase task loop for a project.

        Assumes the task graph has been attached and the project has been
        transitioned into BUILDING.
        """
        graph = self._task_graphs.get(project_id)
        if graph is None:
            raise ValueError(f"No task graph attached for project {project_id}")

        in_flight: set[asyncio.Task] = set()

        while not graph.is_complete():
            ready = graph.get_ready_tasks()
            for task in ready:
                if len(in_flight) >= self.max_parallel_agents:
                    break
                graph.mark_running(task.task_id)
                coro = self._dispatch_task(project_id, task)
                in_flight.add(asyncio.create_task(coro))

            if not in_flight:
                # No work to do this tick — either everything is in_flight or stuck.
                if graph.has_failures():
                    logger.error(
                        "orchestrator.has_failures_no_progress",
                        extra={"project_id": project_id},
                    )
                    break
                await asyncio.sleep(self.poll_interval_seconds)
                continue

            done, in_flight = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for d in done:
                exc = d.exception()
                if exc is not None:
                    logger.exception(
                        "orchestrator.task_dispatch_exception",
                        exc_info=exc,
                        extra={"project_id": project_id},
                    )

        await self.event_bus.publish(
            "project.phase_done",
            {"project_id": project_id, "phase": "BUILDING"},
        )

    # --------------------------- internal helpers --------------------------

    async def _dispatch_task(self, project_id: str, task: TaskSchema) -> AgentResult:
        agent_key = task.agent_type.value if isinstance(task.agent_type, AgentType) else task.agent_type
        agent = self.agents.get(agent_key)
        if agent is None:
            logger.error(
                "orchestrator.no_agent_for_type",
                extra={"agent_type": agent_key, "task_id": task.task_id},
            )
            record_event(
                event_type="failed",
                status="failed",
                agent_type=agent_key,
                project_id=project_id,
                task_id=task.task_id,
                error=f"no agent for {agent_key}",
            )
            graph = self._task_graphs[project_id]
            graph.mark_failed(task.task_id)
            await self.event_bus.publish(
                "agent.failed",
                {"project_id": project_id, "task_id": task.task_id, "error": "no agent"},
            )
            return AgentResult(status="failed", error=f"no agent for {agent_key}")

        async with self._semaphore:
            self._mark_task_started(project_id, task.task_id)
            payload = {"task": task.model_dump(mode="json"), "project_id": project_id}
            result = await agent.run_with_retry(payload)
            self._record_task_result(project_id, task.task_id, result)

        graph = self._task_graphs[project_id]
        if result.status == "success":
            graph.mark_done(task.task_id)
            await self.event_bus.publish(
                "agent.completed",
                {"project_id": project_id, "task_id": task.task_id, "result": result},
            )
        elif result.status in ("needs_human", "blocked"):
            # `blocked` is a hard policy stop, not a transient failure — surface
            # it to a human rather than retrying or marking it failed.
            graph.mark_escalated(task.task_id)
            await self.event_bus.publish(
                "agent.flag_human",
                {"project_id": project_id, "task_id": task.task_id, "result": result},
            )
        else:
            graph.mark_failed(task.task_id)
            await self.event_bus.publish(
                "agent.failed",
                {"project_id": project_id, "task_id": task.task_id, "result": result},
            )
        return result

    def _mark_task_started(self, project_id: str, task_id: str) -> None:
        with Session(get_engine()) as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                row = TaskRow(id=task_id, project_id=project_id, status="running")
            row.status = "running"
            row.started_at = utcnow()
            session.add(row)
            session.commit()

    def _record_task_result(self, project_id: str, task_id: str, result: AgentResult) -> None:
        with Session(get_engine()) as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                row = TaskRow(id=task_id, project_id=project_id)
            row.status = result.status if result.status != "success" else "done"
            row.completed_at = utcnow()
            try:
                if result.payload is not None:
                    row.result = json.dumps(
                        result.payload if isinstance(result.payload, (dict, list)) else str(result.payload)
                    )
            except (TypeError, ValueError):
                row.result = json.dumps({"unserializable": True})
            session.add(row)
            session.commit()

    # ----------------------------- handlers --------------------------------

    async def _on_agent_completed(self, data: dict) -> None:
        logger.info("event.agent.completed", extra=data)

    async def _on_agent_failed(self, data: dict) -> None:
        logger.warning("event.agent.failed", extra=data)

    async def _on_flag_human(self, data: dict) -> None:
        logger.warning("event.agent.flag_human", extra=data)
        project_id = data.get("project_id", "")
        await self.notifier.send_decision_request(
            project_id=project_id,
            decision_type="Flagged Decision",
            context_url=f"{self.notifier.dashboard_url}/projects/{project_id}",
            summary=str(data.get("result", ""))[:140],
        )

    async def _on_validation_complete(self, data: dict) -> None:
        logger.info("event.validation.complete", extra=data)

    async def _on_phase_done(self, data: dict) -> None:
        logger.info("event.project.phase_done", extra=data)

    async def _on_anomaly(self, data: dict) -> None:
        """Manager reaction to a flagged anomaly: log, and escalate critical ones."""
        logger.warning(
            "event.observability.anomaly",
            extra={"anomaly_key": data.get("key"), "severity": data.get("severity"),
                   "agent": data.get("agent")},
        )
        if data.get("severity") == "critical":
            await self.notifier.send_decision_request(
                project_id=data.get("agent", "fleet"),
                decision_type="Anomaly Detected",
                context_url=f"{self.notifier.dashboard_url}/observability",
                summary=str(data.get("message", ""))[:140],
            )


def bootstrap(database_url: Optional[str] = None) -> Orchestrator:
    """Helper to initialise the DB and return a fresh orchestrator."""
    init_db(database_url)
    return Orchestrator()
