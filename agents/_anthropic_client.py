"""Thin wrapper around the Anthropic SDK used by thinking agents.

All thinking agents share this client so we can swap the model or mock it
in tests via the `client_factory` argument on each agent.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    raw: Any = None


class AnthropicClient:
    """Lightweight wrapper. Lazily imports anthropic so tests can mock it out."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from anthropic import Anthropic  # type: ignore
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 8000,
        thinking_budget_tokens: Optional[int] = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if thinking_budget_tokens:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget_tokens}

        msg = client.messages.create(**kwargs)
        # The SDK returns msg.content as a list of content blocks. Extract text.
        text_parts = []
        for block in msg.content:
            block_type = getattr(block, "type", None)
            if block_type == "text" and getattr(block, "text", None):
                text_parts.append(block.text)
            elif block_type == "thinking":
                # Skip extended-thinking blocks — they aren't the answer.
                continue
        return LLMResponse(text="\n".join(text_parts), raw=msg)


# ----------------------------- JSON helpers -----------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def extract_json(text: str) -> dict | list:
    """Extract a JSON object/array from a model response.

    Handles:
    - Plain JSON
    - JSON inside ```json ... ``` fences
    - JSON with surrounding prose
    """
    text = text.strip()
    if not text:
        raise ValueError("Empty model response")

    # Try direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try fenced block.
    m = _JSON_FENCE_RE.search(text)
    if m:
        return json.loads(m.group(1))

    # Last resort: find the first { ... } or [ ... ] balanced span.
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    return json.loads(candidate)

    raise ValueError(f"Could not extract JSON from response: {text[:200]!r}")
