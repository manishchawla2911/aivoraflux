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
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Awaitable, Callable, List, Union

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import Session, select

from core.agent_factory import (
    AgentSpec, CATEGORIES, TOOL_CATALOG, _slugify, auto_craft,
)
from core.guardrails import GUARDRAIL_FEATURES
from core.llm_providers import list_providers
from core.observability import WINDOWS, compute_metrics, detect_anomalies
from core.agent_runtime import run_agent_completion
from core import chat_bridge
from core.chat_bridge import BRIDGE_PLATFORMS
from core.state import (
    Agent, AgentRun, GuardrailProfile, HumanDecision, MarketingContact, OutreachMessage,
    Project, ProviderConfig, Workspace, WorkspaceChannel, WorkspaceChatMessage, WorkspaceMember,
    WorkspaceMemory, WorkspaceProject,
    get_engine, init_db, utcnow,
)
from core import marketing, voice
from core import workspace_memory
from core import workspace_chat
from core import workspace_spawn
from core.cost_estimator import estimate_project_cost, CostEstimate
from core.workspace_factory import create_workspace
from core.workspace_roles import ROLE_CATALOG
from dataclasses import asdict

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
PROJECTS_DIR = Path(os.getenv("PROJECTS_BASE_PATH", "./projects"))

# Maximum characters accepted by the agent run endpoint (SEC-2). Override via env.
MAX_RUN_INPUT_CHARS = int(os.getenv("MAX_RUN_INPUT_CHARS", "20000"))
WORKSPACE_MEMORY_K = int(os.getenv("WORKSPACE_MEMORY_K", "5"))

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
    if len(input) > MAX_RUN_INPUT_CHARS:
        return JSONResponse(
            {"error": f"input too large ({len(input)} chars); max is {MAX_RUN_INPUT_CHARS}"},
            status_code=413,
        )
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        member = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.agent_id == agent_id)
        ).first()
        memory_block = workspace_memory.build_memory_context(
            s, member.workspace_id, input, k=WORKSPACE_MEMORY_K
        ) if member else ""
        result = run_agent_completion(s, agent, user_input=input, extra_system=memory_block)

    if result.blocked:
        msg = (f"⛔ Blocked by guardrails: {result.reason}" if result.block_stage == "input"
               else f"⛔ Output blocked: {result.reason}")
        return JSONResponse({
            "guardrail_verdict": "blocked",
            "reason": result.reason,
            "output": msg,
            "latency_ms": result.latency_ms,
        })

    return JSONResponse({
        "output": result.final_text,
        "guardrail_verdict": "passed",
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "stubbed": result.stubbed,
        "provider": result.provider,
        "model": result.model,
    })


# ─────────────────────────────────────────────────────────────
# Workspaces — an AI company: roster of role agents + shared memory
# ─────────────────────────────────────────────────────────────

@app.get("/workspaces", response_class=HTMLResponse)
async def workspaces_list(request: Request) -> HTMLResponse:
    with Session(get_engine()) as s:
        workspaces = s.exec(
            select(Workspace).order_by(Workspace.created_at.desc())
        ).all()
        counts = {
            w.id: (s.scalar(
                select(func.count()).select_from(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == w.id)
            ) or 0)
            for w in workspaces
        }
    return templates.TemplateResponse(request, "workspaces.html", {
        "workspaces": workspaces,
        "member_counts": counts,
    })


@app.get("/workspaces/new", response_class=HTMLResponse)
async def workspaces_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "workspace_new.html", {
        "roles": ROLE_CATALOG,
    })


@app.post("/workspaces")
async def workspaces_create(request: Request) -> RedirectResponse:
    form = await request.form()
    valid = {r["id"] for r in ROLE_CATALOG}
    selected = [r for r in form.getlist("roles") if r in valid]
    overrides = {}
    for role_id in selected:
        dn = (form.get(f"name_{role_id}") or "").strip()
        if dn:
            overrides[role_id] = {"display_name": dn}
    with Session(get_engine()) as s:
        ws = create_workspace(
            s,
            owner_email=None,
            name=form.get("name", "Untitled").strip() or "Untitled",
            company_description=form.get("company_description", ""),
            mission=form.get("mission", ""),
            selected_roles=selected,
            member_overrides=overrides,
        )
        ws_id = ws.id
    return RedirectResponse(url=f"/workspaces/{ws_id}", status_code=303)


@app.get("/workspaces/{workspace_id}", response_class=HTMLResponse)
async def workspace_detail(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        members = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.order_index)
        ).all()
        agents = {a.id: a for a in s.exec(
            select(Agent).where(Agent.id.in_([m.agent_id for m in members]))
        ).all()} if members else {}
        memories = s.exec(
            select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == workspace_id)
            .order_by(WorkspaceMemory.created_at.desc()).limit(50)
        ).all()
    return templates.TemplateResponse(request, "workspace_detail.html", {
        "ws": ws,
        "members": members,
        "agents": agents,
        "memories": memories,
        "memory_kinds": ["note", "fact", "decision", "goal"],
    })


@app.post("/workspaces/{workspace_id}/memory")
async def workspace_add_memory(workspace_id: str, request: Request) -> RedirectResponse:
    form = await request.form()
    content = (form.get("content") or "").strip()
    if content:
        kind = form.get("kind", "note")
        kind = kind if kind in {"note", "fact", "decision", "goal"} else "note"
        tags = [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]
        with Session(get_engine()) as s:
            if s.get(Workspace, workspace_id) is None:
                raise HTTPException(404, "workspace not found")
            workspace_memory.add_entry(
                s, workspace_id, author="owner", kind=kind, content=content, tags=tags,
            )
    return RedirectResponse(url=f"/workspaces/{workspace_id}", status_code=303)


@app.post("/workspaces/{workspace_id}/archive")
async def workspace_archive(workspace_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        ws.status = "archived"
        ws.updated_at = utcnow()
        s.add(ws)
        s.commit()
    return RedirectResponse(url="/workspaces", status_code=303)


@app.get("/workspaces/{workspace_id}/chat", response_class=HTMLResponse)
async def workspace_chat_view(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        members = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.order_index)
        ).all()
        messages = s.exec(
            select(WorkspaceChatMessage).where(WorkspaceChatMessage.workspace_id == workspace_id)
            .order_by(WorkspaceChatMessage.created_at)
        ).all()
    return templates.TemplateResponse(request, "workspace_chat.html", {
        "ws": ws,
        "members": members,
        "messages": messages,
    })


@app.post("/workspaces/{workspace_id}/chat")
async def workspace_chat_post(workspace_id: str, content: str = Form(...)) -> RedirectResponse:
    text = content.strip()
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if text:
            workspace_chat.post_message(s, workspace_id, author_kind="owner", content=text)
    return RedirectResponse(url=f"/workspaces/{workspace_id}/chat", status_code=303)


@app.post("/workspaces/{workspace_id}/chat/{msg_id}/pin")
async def workspace_chat_pin(workspace_id: str, msg_id: str, request: Request) -> RedirectResponse:
    form = await request.form()
    kind = form.get("kind", "decision")
    kind = kind if kind in {"note", "fact", "decision", "goal"} else "decision"
    with Session(get_engine()) as s:
        msg = s.get(WorkspaceChatMessage, msg_id)
        if msg is None or msg.workspace_id != workspace_id:
            raise HTTPException(404, "message not found")
        entry = workspace_memory.add_entry(
            s, workspace_id, author=msg.author_name, kind=kind,
            content=msg.content, tags=["pinned"],
        )
        msg.pinned_memory_id = entry.id
        s.add(msg)
        s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/chat", status_code=303)


@app.get("/workspaces/{workspace_id}/projects", response_class=HTMLResponse)
async def workspace_projects(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        projects = s.exec(
            select(WorkspaceProject).where(WorkspaceProject.workspace_id == workspace_id)
            .order_by(WorkspaceProject.created_at.desc())
        ).all()
    return templates.TemplateResponse(request, "workspace_projects.html", {
        "ws": ws,
        "projects": projects,
    })


@app.post("/workspaces/{workspace_id}/projects")
async def workspace_project_create(workspace_id: str, name: str = Form(...),
                                   brief: str = Form(""),
                                   client_name: str = Form("")) -> RedirectResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        project = WorkspaceProject(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=name.strip() or "Untitled Project",
            brief=brief.strip(),
            client_name=client_name.strip() or None,
            status="estimating",
        )
        s.add(project)
        s.commit()
        s.refresh(project)

        plan = workspace_spawn.plan_team(project.brief)
        estimate = estimate_project_cost(project.brief, plan.team_size)
        project.estimate_json = estimate.to_json()
        s.add(project)
        s.commit()

        workspace_spawn.spawn_project_team(s, workspace_id, project, plan=plan)
        project.status = "staffed"
        project.updated_at = utcnow()
        s.add(project)
        s.commit()

        workspace_memory.add_entry(
            s, workspace_id, author="CEO", kind="decision",
            content=(f"Opened project '{project.name}' for "
                     f"{project.client_name or 'internal'}. Estimated cost "
                     f"${estimate.cost_usd} ({plan.team_size}-agent team)."),
            tags=["project", "estimate"],
        )
        pid = project.id
    return RedirectResponse(url=f"/workspaces/{workspace_id}/projects/{pid}", status_code=303)


@app.get("/workspaces/{workspace_id}/projects/{project_id}", response_class=HTMLResponse)
async def workspace_project_detail(request: Request, workspace_id: str,
                                   project_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        project = s.get(WorkspaceProject, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise HTTPException(404, "project not found")
        ws = s.get(Workspace, workspace_id)
        team = s.exec(
            select(WorkspaceMember).where(
                (WorkspaceMember.workspace_id == workspace_id)
                & (WorkspaceMember.origin == "spawned")
            ).order_by(WorkspaceMember.order_index)
        ).all()
        agents = {a.id: a for a in s.exec(
            select(Agent).where(Agent.id.in_([m.agent_id for m in team]))
        ).all()} if team else {}
    estimate = CostEstimate.from_json(project.estimate_json) if project.estimate_json else None
    pm = next((m for m in team if m.id == project.pm_member_id), None)
    reports = [m for m in team if pm and m.parent_member_id == pm.id]
    return templates.TemplateResponse(request, "workspace_project_detail.html", {
        "ws": ws,
        "project": project,
        "estimate": estimate,
        "pm": pm,
        "reports": reports,
        "agents": agents,
    })


@app.post("/workspaces/{workspace_id}/projects/{project_id}/archive")
async def workspace_project_archive(workspace_id: str, project_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        project = s.get(WorkspaceProject, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise HTTPException(404, "project not found")
        project.status = "archived"
        project.updated_at = utcnow()
        s.add(project)
        s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/projects", status_code=303)


@app.post("/bridge/{platform}/webhook")
async def bridge_webhook(platform: str, request: Request) -> JSONResponse:
    if platform not in BRIDGE_PLATFORMS:
        raise HTTPException(404, "unknown platform")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        with Session(get_engine()) as s:
            result = chat_bridge.handle_inbound(s, platform, payload)
    except Exception as e:  # never 5xx — platforms retry-storm on errors
        logger.warning("bridge.webhook_error platform=%s err=%s", platform, e)
        result = {"handled": False, "error": str(e)}
    return JSONResponse(result, status_code=200)


@app.get("/workspaces/{workspace_id}/channels", response_class=HTMLResponse)
async def workspace_channels(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        channels = s.exec(
            select(WorkspaceChannel).where(WorkspaceChannel.workspace_id == workspace_id)
            .order_by(WorkspaceChannel.created_at.desc())
        ).all()
    return templates.TemplateResponse(request, "workspace_channels.html", {
        "ws": ws,
        "channels": channels,
        "platforms": BRIDGE_PLATFORMS,
    })


@app.post("/workspaces/{workspace_id}/channels")
async def workspace_channel_link(workspace_id: str, platform: str = Form(...),
                                 external_id: str = Form(...), label: str = Form(""),
                                 token_env: str = Form("")) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if platform in BRIDGE_PLATFORMS and external_id.strip():
            s.add(WorkspaceChannel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                platform=platform,
                external_id=external_id.strip(),
                label=label.strip() or None,
                token_env=token_env.strip() or BRIDGE_PLATFORMS[platform]["token_env"],
            ))
            s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/channels", status_code=303)


@app.post("/workspaces/{workspace_id}/channels/{channel_id}/remove")
async def workspace_channel_remove(workspace_id: str, channel_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        ch = s.get(WorkspaceChannel, channel_id)
        if ch is None or ch.workspace_id != workspace_id:
            raise HTTPException(404, "channel not found")
        ch.active = False
        s.add(ch)
        s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/channels", status_code=303)


@app.get("/workspaces/{workspace_id}/marketing", response_class=HTMLResponse)
async def workspace_marketing(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        contacts = s.exec(
            select(MarketingContact).where(MarketingContact.workspace_id == workspace_id)
            .order_by(MarketingContact.created_at.desc())
        ).all()
        messages = s.exec(
            select(OutreachMessage).where(OutreachMessage.workspace_id == workspace_id)
            .order_by(OutreachMessage.created_at.desc()).limit(50)
        ).all()
        has_marketing = marketing.find_marketing_member(s, workspace_id) is not None
    return templates.TemplateResponse(request, "workspace_marketing.html", {
        "ws": ws,
        "contacts": contacts,
        "messages": messages,
        "has_marketing": has_marketing,
    })


@app.post("/workspaces/{workspace_id}/marketing/contacts")
async def workspace_marketing_add_contact(workspace_id: str, name: str = Form(...),
                                          email: str = Form(""),
                                          company: str = Form(""),
                                          notes: str = Form("")) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if name.strip():
            s.add(MarketingContact(
                id=str(uuid.uuid4()), workspace_id=workspace_id, name=name.strip(),
                email=email.strip() or None, company=company.strip() or None,
                notes=notes.strip() or None,
            ))
            s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/outreach")
async def workspace_marketing_outreach(workspace_id: str, contact_id: str = Form(...),
                                       goal: str = Form(...)) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        member = marketing.find_marketing_member(s, workspace_id)
        contact = s.get(MarketingContact, contact_id)
        if member is not None and contact is not None and contact.workspace_id == workspace_id:
            marketing.send_outreach(s, workspace_id, member, contact, goal.strip())
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/followups/run")
async def workspace_marketing_run_followups(workspace_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        member = marketing.find_marketing_member(s, workspace_id)
        if member is not None:
            for outreach in marketing.due_followups(s, workspace_id):
                marketing.send_followup(s, member, outreach)
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/voice/preview")
async def workspace_marketing_voice_preview(workspace_id: str,
                                            text: str = Form(...)) -> Response:
    audio = voice.synthesize(text.strip() or "Hello")
    return Response(content=audio, media_type="application/octet-stream")


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
