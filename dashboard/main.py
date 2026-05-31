"""FastAPI dashboard — Agent Studio product surface + legacy delivery routes.

Public marketing
    GET  /                              → landing page
    GET  /pricing                       → pricing page

Agent studio (auth-free for demo)
    GET  /dashboard                     → agent list + projects
    GET  /studio                        → manual + auto-craft UI
    POST /agents/manual                 → create from a form
    POST /agents/auto                   → create from a natural-language brief
    GET  /agents/{id}                   → agent detail + run console
    POST /agents/{id}/run               → execute agent (JSON)
    POST /agents/{id}/delete            → delete agent

Settings
    GET  /settings/providers            → LLM provider status

Legacy delivery-pipeline routes (preserved)
    GET  /projects                      → project list
    GET  /projects/{id}                 → project detail
    GET  /projects/{id}/decisions/{did} → decision review
    POST /projects/{id}/decisions/{did} → submit decision
    POST /projects/new                  → create a project
    GET  /projects/{id}/logs            → SSE log stream
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Union

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import Session, select

from core.agent_factory import (
    AgentSpec, CATEGORIES, TOOL_CATALOG, _slugify, auto_craft,
)
from core.guardrails import GUARDRAIL_FEATURES, Guardrails
from core.llm_providers import CompletionRequest, complete, list_providers
from core.observability import WINDOWS, compute_metrics, detect_anomalies, record_event
from core.state import (
    Agent, AgentRun, GuardrailProfile, HumanDecision, Project,
    ProviderConfig, get_engine, init_db, utcnow,
)
from dataclasses import asdict

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
PROJECTS_DIR = Path(os.getenv("PROJECTS_BASE_PATH", "./projects"))

# Maximum characters accepted by the agent run endpoint (SEC-2). Override via env.
MAX_RUN_INPUT_CHARS = int(os.getenv("MAX_RUN_INPUT_CHARS", "20000"))

# Startup-hook registry — lets `main.py` attach the orchestrator boot to the
# same lifespan without a second (deprecated) on_event handler (DEP-2).
StartupHook = Callable[[FastAPI], Union[None, Awaitable[None]]]
_startup_hooks: List[StartupHook] = []


def on_startup(fn: StartupHook) -> StartupHook:
    """Register a callable to run during app startup (see ``lifespan``)."""
    _startup_hooks.append(fn)
    return fn


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for hook in _startup_hooks:
        result = hook(app)
        if inspect.isawaitable(result):
            await result
    yield


app = FastAPI(title="Agent Studio", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _clamp_float(value, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _default_guardrail_profile(session: Session) -> GuardrailProfile:
    """Return (creating if needed) the default 'Standard' guardrail profile."""
    existing = session.exec(
        select(GuardrailProfile).where(GuardrailProfile.name == "Standard")
    ).first()
    if existing:
        return existing
    gp = GuardrailProfile(id=str(uuid.uuid4()), name="Standard",
                          description="Default — all SOTA layers active.")
    session.add(gp)
    session.commit()
    session.refresh(gp)
    return gp


def _persist_agent(spec: AgentSpec) -> Agent:
    with Session(get_engine()) as s:
        profile = _default_guardrail_profile(s)
        kwargs = spec.to_db_kwargs()
        kwargs["guardrail_profile_id"] = profile.id
        agent = Agent(**kwargs)
        s.add(agent)
        s.commit()
        s.refresh(agent)
        return agent


def _admin_gate(request: Request) -> bool:
    """Guard the observability surface. Returns True when the view is *ungated*.

    If ADMIN_TOKEN is set, a matching ``X-Admin-Token`` header or ``?token=``
    query param is required (else 401). If it's unset, the view stays open for
    the demo but the caller renders an "ungated" warning rather than silently
    exposing cost/error data on an auth-free app.
    """
    token = os.getenv("ADMIN_TOKEN", "")
    if not token:
        return True
    supplied = request.headers.get("X-Admin-Token") or request.query_params.get("token")
    if supplied != token:
        raise HTTPException(401, "admin token required")
    return False


# ─────────────────────────────────────────────────────────────
# Marketing
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "landing.html", {
        "providers": list_providers(),
        "guardrail_features": GUARDRAIL_FEATURES,
    })


@app.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pricing.html", {})


# ─────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    with Session(get_engine()) as s:
        agents = s.exec(select(Agent).order_by(Agent.created_at.desc())).all()
        projects = s.exec(select(Project).order_by(Project.created_at.desc())).all()
        since = utcnow() - timedelta(days=1)
        runs_today = s.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= since)
        ) or 0
    return templates.TemplateResponse(request, "dashboard.html", {
        "agents": agents,
        "projects": projects,
        "runs_today": runs_today,
    })


# ─────────────────────────────────────────────────────────────
# Studio — craft agents
# ─────────────────────────────────────────────────────────────

_EXAMPLE_BRIEFS = [
    "An agent that monitors my Stripe failures every morning, drafts retry emails, and posts a Slack summary.",
    "A research assistant that scans Hacker News every hour and emails me a digest of AI startup launches.",
    "A code reviewer that opens GitHub PRs, runs static analysis, and posts inline review comments.",
    "A customer support agent that answers questions from our docs and escalates billing issues to a human.",
]


@app.get("/studio", response_class=HTMLResponse)
async def studio(request: Request, mode: str = "auto") -> HTMLResponse:
    mode = mode if mode in ("auto", "manual") else "auto"
    providers = list_providers()
    # Smart default: prefer the user's local Ollama daemon when it's running,
    # otherwise fall back to Anthropic. Makes the demo work offline.
    default_provider = "anthropic"
    for p in providers:
        if p["id"] == "ollama" and p["available"]:
            default_provider = "ollama"
            break
    return templates.TemplateResponse(request, "studio.html", {
        "mode": mode,
        "providers": providers,
        "default_provider": default_provider,
        "tools": TOOL_CATALOG,
        "categories": CATEGORIES,
        "guardrail_features": GUARDRAIL_FEATURES,
        "examples": _EXAMPLE_BRIEFS,
    })


@app.post("/agents/auto")
async def create_agent_auto(
    brief: str = Form(...),
    provider: str = Form("anthropic"),
    model: str = Form("claude-sonnet-4-6"),
) -> RedirectResponse:
    spec = auto_craft(brief.strip(), provider=provider, model=model)
    agent = _persist_agent(spec)
    return RedirectResponse(url=f"/agents/{agent.id}", status_code=303)


@app.post("/agents/manual")
async def create_agent_manual(request: Request) -> RedirectResponse:
    form = await request.form()
    selected_tools = [t for t in form.getlist("tools") if t in {x["id"] for x in TOOL_CATALOG}]
    spec = AgentSpec(
        name=form.get("name", "Untitled").strip() or "Untitled",
        description=form.get("description", "").strip(),
        category=form.get("category", "other"),
        system_prompt=form.get("system_prompt", "").strip(),
        model_provider=form.get("model_provider", "anthropic"),
        model_name=form.get("model_name", "claude-sonnet-4-6"),
        temperature=_clamp_float(form.get("temperature", 0.7), 0.7, 0.0, 2.0),
        max_tokens=_clamp_int(form.get("max_tokens", 2048), 2048, 1, 32000),
        tools=selected_tools,
        avatar_emoji=(form.get("avatar_emoji") or "🤖")[:2],
        crafted_mode="manual",
    )
    agent = _persist_agent(spec)
    return RedirectResponse(url=f"/agents/{agent.id}", status_code=303)


# ─────────────────────────────────────────────────────────────
# Agent detail + run
# ─────────────────────────────────────────────────────────────

@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, "agent not found")
        runs = s.exec(
            select(AgentRun).where(AgentRun.agent_id == agent_id)
            .order_by(AgentRun.created_at.desc()).limit(50)
        ).all()
    tool_ids = []
    try:
        tool_ids = json.loads(agent.tools) if agent.tools else []
    except Exception:
        pass
    tool_labels = [t["label"] for t in TOOL_CATALOG if t["id"] in tool_ids]
    return templates.TemplateResponse(request, "agent.html", {
        "agent": agent,
        "agent_tools": tool_labels,
        "runs": runs,
        "base_url": str(request.base_url).rstrip("/"),
    })


@app.post("/agents/{agent_id}/run")
async def run_agent(agent_id: str, input: str = Form(...)) -> JSONResponse:
    t_start = time.time()
    if len(input) > MAX_RUN_INPUT_CHARS:
        return JSONResponse(
            {"error": f"input too large ({len(input)} chars); max is {MAX_RUN_INPUT_CHARS}"},
            status_code=413,
        )
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        profile = s.get(GuardrailProfile, agent.guardrail_profile_id) if agent.guardrail_profile_id else None

    rails = Guardrails(profile=profile)
    in_verdict = rails.check_input(agent_id, input)
    if not in_verdict.allowed:
        _save_run(agent_id, input, "", verdict="blocked", reason=in_verdict.reason, latency=0,
                  agent_name=agent.name, agent_type=agent.category)
        return JSONResponse({
            "guardrail_verdict": "blocked",
            "reason": in_verdict.reason,
            "output": f"⛔ Blocked by guardrails: {in_verdict.reason}",
            "latency_ms": int((time.time() - t_start) * 1000),
        })

    req = CompletionRequest(
        system=agent.system_prompt,
        user=input,
        model=agent.model_name,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
    resp = complete(req, agent.model_provider)

    out_verdict = rails.check_output(resp.text)
    if not out_verdict.allowed:
        _save_run(agent_id, input, resp.text, verdict="blocked",
                  reason=out_verdict.reason, latency=resp.latency_ms,
                  in_tok=resp.input_tokens, out_tok=resp.output_tokens, cost=resp.cost_usd,
                  agent_name=agent.name, agent_type=agent.category)
        return JSONResponse({
            "guardrail_verdict": "blocked",
            "reason": out_verdict.reason,
            "output": f"⛔ Output blocked: {out_verdict.reason}",
            "latency_ms": resp.latency_ms,
        })

    final_text = out_verdict.redacted_text or resp.text
    _save_run(agent_id, input, final_text, verdict="passed", latency=resp.latency_ms,
              in_tok=resp.input_tokens, out_tok=resp.output_tokens, cost=resp.cost_usd,
              agent_name=agent.name, agent_type=agent.category)

    return JSONResponse({
        "output": final_text,
        "guardrail_verdict": "passed",
        "latency_ms": resp.latency_ms,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "cost_usd": resp.cost_usd,
        "stubbed": resp.stubbed,
        "provider": resp.provider,
        "model": resp.model,
    })


def _save_run(agent_id: str, inp: str, out: str, *, verdict: str,
              reason: Optional[str] = None, latency: int = 0,
              in_tok: int = 0, out_tok: int = 0, cost: float = 0.0,
              agent_name: Optional[str] = None, agent_type: Optional[str] = None) -> None:
    with Session(get_engine()) as s:
        s.add(AgentRun(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            input_text=inp,
            output_text=out,
            latency_ms=latency,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            guardrail_verdict=verdict,
            guardrail_reason=reason,
        ))
        s.commit()
    # Mirror the run into the unified observability stream (fail-open, no raw text).
    record_event(
        event_type="guardrail_block" if verdict == "blocked" else "run",
        source="studio",
        status="blocked" if verdict == "blocked" else "passed",
        agent_id=agent_id,
        agent_name=agent_name,
        agent_type=agent_type,
        guardrail_verdict=verdict,
        error=reason,
        duration_ms=latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        input_chars=len(inp or ""),
        output_chars=len(out or ""),
    )


@app.post("/agents/{agent_id}/delete")
async def delete_agent(agent_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        a = s.get(Agent, agent_id)
        if a:
            s.delete(a)
            s.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────

@app.get("/settings/providers", response_class=HTMLResponse)
async def settings_providers(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings_providers.html", {
        "providers": list_providers(),
    })


# ─────────────────────────────────────────────────────────────
# Observability — metrics + anomaly dashboard (admin)
# ─────────────────────────────────────────────────────────────

@app.get("/observability", response_class=HTMLResponse)
async def observability(request: Request, window: str = "24h") -> HTMLResponse:
    ungated = _admin_gate(request)
    window = window if window in WINDOWS else "24h"
    secs = WINDOWS[window]
    metrics = compute_metrics(secs)
    anomalies = [asdict(a) for a in detect_anomalies(secs)]
    critical = sum(1 for a in anomalies if a["severity"] == "critical")
    return templates.TemplateResponse(request, "observability.html", {
        "metrics": metrics,
        "anomalies": anomalies,
        "critical_count": critical,
        "window": window,
        "windows": list(WINDOWS.keys()),
        "ungated": ungated,
    })


@app.get("/observability/api/metrics")
async def observability_metrics_api(request: Request, window: str = "24h") -> JSONResponse:
    _admin_gate(request)
    return JSONResponse(compute_metrics(WINDOWS.get(window, 86400)))


@app.get("/observability/api/anomalies")
async def observability_anomalies_api(request: Request, window: str = "24h") -> JSONResponse:
    _admin_gate(request)
    anomalies = [asdict(a) for a in detect_anomalies(WINDOWS.get(window, 86400))]
    return JSONResponse({"anomalies": anomalies})


# ─────────────────────────────────────────────────────────────
# Legacy: delivery projects (preserved at /projects/*)
# ─────────────────────────────────────────────────────────────

@app.get("/projects", response_class=HTMLResponse)
async def project_list(request: Request) -> HTMLResponse:
    with Session(get_engine()) as s:
        projects = s.exec(select(Project).order_by(Project.created_at.desc())).all()
    return templates.TemplateResponse(request, "index.html", {"projects": projects})


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        row = s.get(Project, project_id)
        if not row:
            raise HTTPException(404, "project not found")
        decisions = s.exec(
            select(HumanDecision).where(HumanDecision.project_id == project_id)
        ).all()
    return templates.TemplateResponse(request, "project.html", {
        "project": row, "decisions": decisions,
    })


@app.post("/projects/new")
async def create_project(
    name: str = Form(...),
    client_name: str = Form(""),
) -> RedirectResponse:
    pid = str(uuid.uuid4())
    with Session(get_engine()) as s:
        s.add(Project(id=pid, name=name, client_name=client_name, status="CREATED"))
        s.commit()
    return RedirectResponse(url=f"/projects/{pid}", status_code=303)


@app.get("/projects/{project_id}/decisions/{decision_id}", response_class=HTMLResponse)
async def view_decision(request: Request, project_id: str, decision_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        decision = s.get(HumanDecision, decision_id)
        if not decision or decision.project_id != project_id:
            raise HTTPException(404, "decision not found")
        project = s.get(Project, project_id)
    options = json.loads(decision.options) if decision.options else []
    context = json.loads(decision.context) if decision.context else {}
    return templates.TemplateResponse(request, "decision.html", {
        "project": project, "decision": decision,
        "options": options, "context": context,
    })


@app.post("/projects/{project_id}/decisions/{decision_id}")
async def submit_decision(
    project_id: str,
    decision_id: str,
    chosen_option: str = Form(...),
) -> RedirectResponse:
    with Session(get_engine()) as s:
        decision = s.get(HumanDecision, decision_id)
        if not decision or decision.project_id != project_id:
            raise HTTPException(404, "decision not found")
        decision.chosen_option = chosen_option
        decision.decided_at = utcnow()
        s.add(decision)
        s.commit()
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/logs")
async def stream_logs(project_id: str):
    """Server-Sent Events stream of the orchestrator log file for this project."""
    log_path = PROJECTS_DIR / project_id / "logs" / "orchestrator.log"

    async def event_gen():
        last_size = 0
        for _ in range(600):
            if log_path.exists():
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if len(text) > last_size:
                    new = text[last_size:]
                    last_size = len(text)
                    for line in new.splitlines():
                        yield f"data: {line}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
