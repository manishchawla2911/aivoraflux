"""Clarification Agent — converts raw client brief into a structured PRD.

Model: claude-sonnet-4-6
Behaviour: supports multiple rounds; passes previous_answers back to the LLM.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from agents._anthropic_client import AnthropicClient, extract_json
from agents.base_agent import AgentResult, BaseAgent
from schemas.prd import ClarificationInput, ClarificationOutput

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ClarificationAgent(BaseAgent):
    name = "clarification_agent"
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
        self._prompt = (prompt_path or PROMPTS_DIR / "clarification_agent.md").read_text(
            encoding="utf-8"
        )

    async def run(self, input: dict) -> AgentResult:
        # 1. Validate input.
        try:
            payload = ClarificationInput.model_validate(input)
        except ValidationError as e:
            return AgentResult(status="failed", error=f"invalid input: {e}")

        user_msg = self._build_user_message(payload)

        # 2. Call LLM with one re-prompt if schema validation fails.
        validation_error: Optional[str] = None
        for attempt in range(2):
            full_user = user_msg
            if validation_error:
                full_user = (
                    user_msg
                    + "\n\nYour previous response failed schema validation with this error:\n"
                    + validation_error
                    + "\nReturn a fully valid JSON object matching ClarificationOutput."
                )

            response = self._client.complete(
                model=self.model, system=self._prompt, user=full_user, max_tokens=8000
            )
            try:
                data = extract_json(response.text)
            except ValueError as e:
                validation_error = f"json parse: {e}"
                continue

            try:
                output = ClarificationOutput.model_validate(data)
            except ValidationError as e:
                validation_error = str(e)
                continue

            if output.status == "needs_clarification":
                return AgentResult(
                    status="needs_human" if not output.clarification_questions else "success",
                    payload=output.model_dump(mode="json"),
                )
            return AgentResult(status="success", payload=output.model_dump(mode="json"))

        return AgentResult(
            status="failed",
            error=f"schema validation failed after retry: {validation_error}",
        )

    def _build_user_message(self, payload: ClarificationInput) -> str:
        parts = [
            f"CLIENT: {payload.client_name}",
            f"BUDGET: {payload.budget_range}",
            f"TIMELINE: {payload.timeline_weeks} weeks",
        ]
        if payload.preferred_stack:
            parts.append(f"PREFERRED STACK: {payload.preferred_stack}")
        parts.append("\nRAW BRIEF:\n" + payload.raw_brief)
        if payload.previous_answers:
            parts.append("\nPREVIOUS Q&A:")
            for qa in payload.previous_answers:
                parts.append(json.dumps(qa))
        parts.append(
            "\nReturn a JSON object matching ClarificationOutput. "
            "If you have enough information, set status='prd_ready' and produce the full PRD. "
            "Otherwise return status='needs_clarification' with up to 5 questions."
        )
        return "\n".join(parts)
