"""Shared agent completion core: guardrails → complete → persist + telemetry.

Extracted from dashboard.main.run_agent so the run endpoint AND internal chat
drive one identical, guardrailed path. Calls llm_providers.complete via the module
attribute so a single monkeypatch point (core.llm_providers.complete) covers both
callers in tests.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session

from core import llm_providers
from core.guardrails import Guardrails
from core.llm_providers import CompletionRequest
from core.observability import record_event
from core.state import Agent, AgentRun, GuardrailProfile


@dataclass
class RunResult:
    final_text: str
    verdict: str                    # passed | blocked
    blocked: bool
    block_stage: Optional[str]      # input | output | None
    reason: Optional[str]
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stubbed: bool
    provider: str
    model: str


def _persist_run(session: Session, agent: Agent, inp: str, out: str, *,
                 verdict: str, reason: Optional[str], latency: int,
                 in_tok: int, out_tok: int, cost: float) -> None:
    session.add(AgentRun(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        input_text=inp,
        output_text=out,
        latency_ms=latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        guardrail_verdict=verdict,
        guardrail_reason=reason,
    ))
    session.commit()
    record_event(
        event_type="guardrail_block" if verdict == "blocked" else "run",
        source="studio",
        status="blocked" if verdict == "blocked" else "passed",
        agent_id=agent.id,
        agent_name=agent.name,
        agent_type=agent.category,
        guardrail_verdict=verdict,
        error=reason,
        duration_ms=latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        input_chars=len(inp or ""),
        output_chars=len(out or ""),
    )


def run_agent_completion(session: Session, agent: Agent, *, user_input: str,
                         extra_system: str = "",
                         profile: Optional[GuardrailProfile] = None) -> RunResult:
    """Run one guardrailed completion for `agent`, persisting the run. Never raises
    on a guardrail block — returns a blocked RunResult instead."""
    t0 = time.time()
    if profile is None and agent.guardrail_profile_id:
        profile = session.get(GuardrailProfile, agent.guardrail_profile_id)
    rails = Guardrails(profile=profile)

    in_verdict = rails.check_input(agent.id, user_input)
    if not in_verdict.allowed:
        latency = int((time.time() - t0) * 1000)
        _persist_run(session, agent, user_input, "", verdict="blocked",
                     reason=in_verdict.reason, latency=latency, in_tok=0, out_tok=0, cost=0.0)
        return RunResult(final_text="", verdict="blocked", blocked=True,
                         block_stage="input", reason=in_verdict.reason, latency_ms=latency,
                         input_tokens=0, output_tokens=0, cost_usd=0.0, stubbed=False,
                         provider="", model=agent.model_name)

    system_prompt = f"{extra_system}\n\n{agent.system_prompt}" if extra_system else agent.system_prompt
    req = CompletionRequest(
        system=system_prompt,
        user=user_input,
        model=agent.model_name,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
    resp = llm_providers.complete(req, agent.model_provider)

    out_verdict = rails.check_output(resp.text)
    if not out_verdict.allowed:
        _persist_run(session, agent, user_input, resp.text, verdict="blocked",
                     reason=out_verdict.reason, latency=resp.latency_ms,
                     in_tok=resp.input_tokens, out_tok=resp.output_tokens, cost=resp.cost_usd)
        return RunResult(final_text="", verdict="blocked", blocked=True,
                         block_stage="output", reason=out_verdict.reason,
                         latency_ms=resp.latency_ms, input_tokens=resp.input_tokens,
                         output_tokens=resp.output_tokens, cost_usd=resp.cost_usd,
                         stubbed=resp.stubbed, provider=resp.provider, model=resp.model)

    final_text = out_verdict.redacted_text or resp.text
    _persist_run(session, agent, user_input, final_text, verdict="passed", reason=None,
                 latency=resp.latency_ms, in_tok=resp.input_tokens,
                 out_tok=resp.output_tokens, cost=resp.cost_usd)
    return RunResult(final_text=final_text, verdict="passed", blocked=False,
                     block_stage=None, reason=None, latency_ms=resp.latency_ms,
                     input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                     cost_usd=resp.cost_usd, stubbed=resp.stubbed,
                     provider=resp.provider, model=resp.model)
