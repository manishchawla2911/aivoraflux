"""Architect Agent — produces a complete technical architecture from an approved PRD.

Model: claude-opus-4-5 with extended thinking (budget_tokens=8000).
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

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ArchitectAgent(BaseAgent):
    name = "architect_agent"
    model = "claude-opus-4-5"

    def __init__(
        self,
        client: Optional[AnthropicClient] = None,
        client_factory: Optional[Callable[[], AnthropicClient]] = None,
        prompt_path: Optional[Path] = None,
        thinking_budget_tokens: int = 8000,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._client = client or (client_factory() if client_factory else AnthropicClient())
        self._prompt = (prompt_path or PROMPTS_DIR / "architect_agent.md").read_text(
            encoding="utf-8"
        )
        self._thinking_budget = thinking_budget_tokens

    async def run(self, input: dict) -> AgentResult:
        # Input contract: { prd, budget_usd, timeline_weeks, deployment_target }
        try:
            prd_dict = input["prd"]
            budget_usd = input["budget_usd"]
            timeline_weeks = input["timeline_weeks"]
            deployment_target = input["deployment_target"]
        except KeyError as e:
            return AgentResult(status="failed", error=f"missing input field: {e}")

        try:
            prd = PRD.model_validate(prd_dict)
        except ValidationError as e:
            return AgentResult(status="failed", error=f"invalid PRD: {e}")

        user_msg = (
            f"BUDGET: ${budget_usd}\n"
            f"TIMELINE: {timeline_weeks} weeks\n"
            f"DEPLOYMENT TARGET: {deployment_target}\n\n"
            "PRD:\n"
            + prd.model_dump_json(indent=2)
            + "\n\nReturn a JSON object matching the Architecture schema. "
            "Include all API contracts, full DB schema, full folder structure."
        )

        validation_error: Optional[str] = None
        for _ in range(2):
            full_user = user_msg
            if validation_error:
                full_user = (
                    user_msg
                    + "\n\nYour previous response failed schema validation with: "
                    + validation_error
                    + "\nReturn a fully valid JSON matching Architecture."
                )
            response = self._client.complete(
                model=self.model,
                system=self._prompt,
                user=full_user,
                max_tokens=16000,
                thinking_budget_tokens=self._thinking_budget,
            )
            try:
                data = extract_json(response.text)
            except ValueError as e:
                validation_error = f"json parse: {e}"
                continue
            try:
                arch = Architecture.model_validate(data)
            except ValidationError as e:
                validation_error = str(e)
                continue
            return AgentResult(status="success", payload=arch.model_dump(mode="json"))

        return AgentResult(
            status="failed",
            error=f"schema validation failed after retry: {validation_error}",
        )
