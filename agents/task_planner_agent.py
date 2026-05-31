"""Task Planner Agent — breaks the architecture into a parallelisable task graph.

Model: claude-sonnet-4-6.
Post-processing: enforce that no two tasks in the same parallel batch share a file.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from agents._anthropic_client import AnthropicClient, extract_json
from agents.base_agent import AgentResult, BaseAgent
from schemas.architecture import Architecture
from schemas.prd import PRD
from schemas.task import TaskGraph

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class TaskPlannerAgent(BaseAgent):
    name = "task_planner_agent"
    model = "claude-sonnet-4-6"

    def __init__(
        self,
        client: Optional[AnthropicClient] = None,
        client_factory: Optional[Callable[[], AnthropicClient]] = None,
        prompt_path: Optional[Path] = None,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._client = client or (client_factory() if client_factory else AnthropicClient())
        self._prompt = (prompt_path or PROMPTS_DIR / "task_planner_agent.md").read_text(
            encoding="utf-8"
        )

    async def run(self, input: dict) -> AgentResult:
        try:
            arch = Architecture.model_validate(input["architecture"])
            prd = PRD.model_validate(input["prd"])
            project_id = input.get("project_id", "proj-unknown")
        except (KeyError, ValidationError) as e:
            return AgentResult(status="failed", error=f"invalid input: {e}")

        user_msg = (
            f"PROJECT_ID: {project_id}\n\n"
            "ARCHITECTURE:\n"
            + arch.model_dump_json(indent=2)
            + "\n\nPRD:\n"
            + prd.model_dump_json(indent=2)
            + "\n\nReturn a JSON object matching the TaskGraph schema. "
            "Set project_id to the value above. Group tasks into parallel batches in execution_order."
        )

        validation_error: Optional[str] = None
        for _ in range(2):
            full_user = user_msg
            if validation_error:
                full_user = (
                    user_msg
                    + "\n\nYour previous response failed validation with: "
                    + validation_error
                    + "\nReturn a fully valid JSON matching TaskGraph."
                )
            response = self._client.complete(
                model=self.model, system=self._prompt, user=full_user, max_tokens=16000
            )
            try:
                data = extract_json(response.text)
            except ValueError as e:
                validation_error = f"json parse: {e}"
                continue
            try:
                graph = TaskGraph.model_validate(data)
            except ValidationError as e:
                validation_error = str(e)
                continue

            overlap = self._find_file_overlaps(graph)
            if overlap:
                validation_error = f"parallel batch file overlap: {overlap}"
                continue

            return AgentResult(status="success", payload=graph.model_dump(mode="json"))

        return AgentResult(
            status="failed",
            error=f"schema validation failed after retry: {validation_error}",
        )

    @staticmethod
    def _find_file_overlaps(graph: TaskGraph) -> Optional[dict]:
        """Return offending task pairs if two tasks in the same batch share a file."""
        task_files = {
            t.task_id: set(t.files_to_create) | set(t.files_to_modify) for t in graph.tasks
        }
        for batch_idx, batch in enumerate(graph.execution_order):
            seen: dict[str, str] = {}
            for tid in batch:
                for f in task_files.get(tid, set()):
                    if f in seen and seen[f] != tid:
                        return {
                            "batch": batch_idx,
                            "file": f,
                            "tasks": [seen[f], tid],
                        }
                    seen[f] = tid
        return None
