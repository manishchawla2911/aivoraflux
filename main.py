"""Top-level entry point. Starts FastAPI + the orchestrator loop together.

The orchestrator is launched on FastAPI startup as a background asyncio task.
Project task graphs that have been persisted will be picked up and resumed.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from agents.architect_agent import ArchitectAgent
from agents.backend_agent import BackendAgent
from agents.clarification_agent import ClarificationAgent
from agents.devops_agent import DevOpsAgent
from agents.docs_delivery_agent import DocsDeliveryAgent
from agents.frontend_agent import FrontendAgent
from agents.integration_agent import IntegrationAgent
from agents.review_agent import ReviewAgent
from agents.security_quality_agent import SecurityQualityAgent
from agents.task_planner_agent import TaskPlannerAgent
from agents.test_writer_agent import TestWriterAgent
from core.event_bus import EventBus
from core.notifier import SlackNotifier
from core.observability import run_sweep_loop
from core.orchestrator import Orchestrator
from dashboard.main import app, on_startup

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def build_orchestrator() -> Orchestrator:
    """Wire up every agent and the event bus into a single Orchestrator."""
    bus = EventBus()
    notifier = SlackNotifier()
    orch = Orchestrator(event_bus=bus, notifier=notifier)

    # Thinking agents — keyed by phase rather than agent_type.
    orch.register_agent("clarification", ClarificationAgent())
    orch.register_agent("architect", ArchitectAgent())
    orch.register_agent("task_planner", TaskPlannerAgent())

    # Builder agents — keyed by AgentType.value so the task dispatcher can find them.
    orch.register_agent("backend", BackendAgent())
    orch.register_agent("frontend", FrontendAgent())
    orch.register_agent("integration", IntegrationAgent())
    orch.register_agent("devops", DevOpsAgent())

    # Validation agents — orchestrator triggers these explicitly after each task.
    orch.register_agent("test_writer", TestWriterAgent())
    orch.register_agent("security_quality", SecurityQualityAgent())

    # Review + delivery.
    orch.register_agent("review", ReviewAgent())
    orch.register_agent("docs_delivery", DocsDeliveryAgent())
    return orch


# DB init happens inside the dashboard app's lifespan; we just attach the
# orchestrator boot to the same lifespan via the startup-hook registry (DEP-2).
@on_startup
async def _start_orchestrator(app) -> None:
    orch = build_orchestrator()
    app.state.orchestrator = orch
    logger.info("orchestrator.booted")

    # Opt-in proactive anomaly sweep. Default 0 = disabled (metrics/anomalies
    # are still computed on every dashboard load); set OBSERVABILITY_SWEEP_SECONDS
    # to enable the manager-facing periodic flagging loop.
    interval = int(os.getenv("OBSERVABILITY_SWEEP_SECONDS", "0"))
    if interval > 0:
        window = int(os.getenv("OBSERVABILITY_SWEEP_WINDOW_SECONDS", "3600"))
        app.state.observability_sweep = asyncio.create_task(
            run_sweep_loop(orch.event_bus, interval, window)
        )
        logger.info("observability.sweep_enabled", extra={"interval": interval})


def main() -> None:
    # Bind to loopback by default (SEC-1); set HOST=0.0.0.0 to expose it.
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
