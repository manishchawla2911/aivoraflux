"""Abstract base class for all agents.

Provides retry logic, structured logging, and a uniform AgentResult contract.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from core.observability import record_event

logger = logging.getLogger(__name__)

# Map a terminal AgentResult.status to a telemetry event_type.
_TERMINAL_EVENT = {
    "success": "completed",
    "failed": "failed",
    "needs_human": "escalated",
    "blocked": "escalated",
}


def _obs_ctx(input: dict) -> dict:
    """Pull project/task context out of a dispatch payload, defensively."""
    ctx = {"project_id": None, "task_id": None, "agent_type": None}
    try:
        if isinstance(input, dict):
            ctx["project_id"] = input.get("project_id")
            task = input.get("task")
            if isinstance(task, dict):
                ctx["task_id"] = task.get("task_id")
                ctx["agent_type"] = task.get("agent_type")
    except Exception:  # noqa: BLE001 — context extraction must never break a run
        pass
    return ctx


@dataclass
class AgentResult:
    status: str  # "success" | "needs_human" | "failed" | "blocked"
    payload: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base. Subclasses implement run().

    Retry policy: 3 attempts with exponential backoff (2s, 8s, 32s).
    """

    name: str = "base"
    model: str = ""
    max_retries: int = 3

    def __init__(
        self,
        name: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: Optional[int] = None,
    ):
        if name is not None:
            self.name = name
        if model is not None:
            self.model = model
        if max_retries is not None:
            self.max_retries = max_retries

    @abstractmethod
    async def run(self, input: dict) -> AgentResult:
        """Concrete agent logic. Must return an AgentResult."""
        raise NotImplementedError

    async def run_with_retry(self, input: dict) -> AgentResult:
        """Wraps run() with retry logic.

        - Stops early on success or needs_human.
        - Retries on `failed` status or raised exception.
        - Backoff: 2s, 8s, 32s (2 ** attempt * 2).
        """
        last_error: Optional[str] = None
        start = time.monotonic()
        ctx = _obs_ctx(input)

        for attempt in range(self.max_retries):
            attempt_start = time.monotonic()
            try:
                logger.info(
                    "agent.attempt",
                    extra={"agent": self.name, "attempt": attempt + 1, "max": self.max_retries},
                )
                result = await self.run(input)
                duration_ms = int((time.monotonic() - attempt_start) * 1000)
                if result.duration_ms == 0:
                    result.duration_ms = duration_ms

                if result.status in ("success", "needs_human", "blocked"):
                    logger.info(
                        "agent.completed",
                        extra={
                            "agent": self.name,
                            "status": result.status,
                            "attempt": attempt + 1,
                            "duration_ms": duration_ms,
                        },
                    )
                    record_event(
                        event_type=_TERMINAL_EVENT.get(result.status, "completed"),
                        status=result.status,
                        agent_name=self.name,
                        agent_type=ctx["agent_type"],
                        project_id=ctx["project_id"],
                        task_id=ctx["task_id"],
                        attempt=attempt + 1,
                        retry_count=attempt,
                        duration_ms=result.duration_ms,
                        error=result.error,
                    )
                    return result

                last_error = result.error or "unspecified failure"
                logger.warning(
                    "agent.failed_attempt",
                    extra={
                        "agent": self.name,
                        "attempt": attempt + 1,
                        "error": last_error,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — base class catches all
                last_error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "agent.exception",
                    extra={"agent": self.name, "attempt": attempt + 1},
                )

            # Record each failed attempt so retry-storms are visible in telemetry.
            record_event(
                event_type="attempt",
                status="failed",
                agent_name=self.name,
                agent_type=ctx["agent_type"],
                project_id=ctx["project_id"],
                task_id=ctx["task_id"],
                attempt=attempt + 1,
                duration_ms=int((time.monotonic() - attempt_start) * 1000),
                error=last_error,
            )

            # Backoff before next attempt (skip after the final attempt).
            if attempt < self.max_retries - 1:
                backoff = 2 * (4 ** attempt)  # 2s, 8s, 32s … per spec
                await asyncio.sleep(backoff)

        total_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "agent.max_retries_exceeded",
            extra={"agent": self.name, "error": last_error, "duration_ms": total_ms},
        )
        record_event(
            event_type="failed",
            status="failed",
            agent_name=self.name,
            agent_type=ctx["agent_type"],
            project_id=ctx["project_id"],
            task_id=ctx["task_id"],
            attempt=self.max_retries,
            retry_count=max(0, self.max_retries - 1),
            duration_ms=total_ms,
            error=f"Max retries exceeded: {last_error}",
        )
        return AgentResult(
            status="failed",
            error=f"Max retries exceeded: {last_error}",
            duration_ms=total_ms,
        )
