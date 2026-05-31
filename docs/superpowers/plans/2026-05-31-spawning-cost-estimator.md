# CEO Spawning + Cost Estimator (Subsystem G) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A workspace can open projects; opening one deterministically spawns a team (CEO → PM → developers/auditor) and produces a client-facing cost quote from `PROVIDER_CATALOG` pricing.

**Architecture:** A shared `instantiate_member` helper (extracted from `workspace_factory`) creates both seed and spawned members as ordinary Agent+WorkspaceMember rows. `core/workspace_spawn.py` adds a spawnable role catalog, `spawn_member`, a deterministic `plan_team`, and `spawn_project_team`. `core/cost_estimator.py` is a pure deterministic quote. A new `WorkspaceProject` table + project routes/UI tie it together. All deterministic and offline-testable.

**Tech Stack:** Python 3, SQLModel/SQLite, FastAPI + Jinja2, pytest (`asyncio_mode=auto`). Reuses `AgentSpec.to_db_kwargs`, `ROLE_CATALOG`/`TOOL_CATALOG`, `PROVIDER_CATALOG`, `workspace_memory`.

**Spec:** `docs/superpowers/specs/2026-05-31-spawning-cost-estimator-design.md`

**Conventions:** `utcnow()` not `datetime.utcnow()`; string PKs, JSON-in-text; no `extra=` with a `message` key in logging; tests offline (`EMBEDDING_BACKEND=stub`). After each task: `pytest tests/ -v` green.

---

## File Structure

**New:** `core/workspace_spawn.py`, `core/cost_estimator.py`,
`dashboard/templates/workspace_projects.html`, `workspace_project_new.html`,
`workspace_project_detail.html`, `tests/test_workspace_spawn.py`.

**Modified:** `core/state.py` (WorkspaceProject + 2 WorkspaceMember columns),
`core/workspace_factory.py` (extract `instantiate_member`), `dashboard/main.py` (project
routes), `dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

---

## Task 1: Data model — WorkspaceProject + member hierarchy columns

**Files:**
- Modify: `core/state.py`
- Test: `tests/test_workspace_spawn.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspace_spawn.py`:

```python
"""Tests for CEO spawning + cost estimator (subsystem G)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceProject,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'spawn.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_project_and_member_hierarchy_columns(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(Agent(id="a1", name="PM", system_prompt="pm"))
        s.add(WorkspaceMember(
            id="m1", workspace_id="w1", agent_id="a1", role="project_manager",
            parent_member_id="ceo1", origin="spawned",
        ))
        s.add(WorkspaceProject(
            id="p1", workspace_id="w1", name="Launch", brief="Build a landing page",
            client_name="Globex", pm_member_id="m1",
        ))
        s.commit()
    with Session(db) as s:
        m = s.get(WorkspaceMember, "m1")
        p = s.get(WorkspaceProject, "p1")
        assert m.parent_member_id == "ceo1" and m.origin == "spawned"
        assert p.status == "estimating" and p.pm_member_id == "m1"
        assert p.estimate_json is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py::test_project_and_member_hierarchy_columns -v`
Expected: FAIL with `ImportError: cannot import name 'WorkspaceProject' from 'core.state'`

- [ ] **Step 3: Edit `core/state.py`**

(a) Add two columns to the existing `WorkspaceMember` class (after `order_index`, before `created_at`):

```python
    parent_member_id: Optional[str] = None     # member that spawned this one
    origin: str = "seed"                        # seed | spawned
```

(b) Add a new table immediately AFTER `WorkspaceChatMessage` and BEFORE the `# Observability` banner:

```python
class WorkspaceProject(SQLModel, table=True):
    """A unit of work in a workspace; anchors a spawned team + a cost quote."""
    __tablename__ = "workspace_project"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    brief: str = ""
    client_name: Optional[str] = None
    status: str = "estimating"                  # estimating | staffed | archived
    pm_member_id: Optional[str] = None          # spawned PM's WorkspaceMember.id
    estimate_json: Optional[str] = None         # JSON CostEstimate (client quote)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_spawn.py::test_project_and_member_hierarchy_columns -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS (cycle-1 member tests unaffected — new columns are nullable/defaulted)

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/test_workspace_spawn.py
git commit -m "feat(spawn): add WorkspaceProject table + member hierarchy columns"
```

---

## Task 2: Extract `instantiate_member` from the factory

**Files:**
- Modify: `core/workspace_factory.py`
- Test: `tests/test_workspace_spawn.py` (append)

### Background
`create_workspace` currently inlines, per role: build `AgentSpec` → persist `Agent` →
persist `WorkspaceMember`. We extract that into `instantiate_member` so spawning (Task 3)
reuses it. `create_workspace`'s observable behavior must not change (cycle-1 tests guard).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_spawn.py`:

```python
from core.workspace_factory import instantiate_member, _default_guardrail_profile_id
from core.workspace_roles import get_role


def test_instantiate_member_sets_origin_and_parent(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.commit()
        gid = _default_guardrail_profile_id(s)
        member = instantiate_member(
            s, "w1", get_role("ceo"), workspace_name="Acme",
            order_index=0, origin="seed", guardrail_id=gid, owner_email="o@x.com",
        )
        assert member.role == "ceo"
        assert member.origin == "seed"
        assert member.parent_member_id is None
        agent = s.get(Agent, member.agent_id)
        assert agent is not None and agent.name == "CEO"

        spawned = instantiate_member(
            s, "w1", get_role("cto"), workspace_name="Acme",
            display_name="Custom CTO", order_index=1, parent_member_id=member.id,
            origin="spawned", guardrail_id=gid, owner_email="o@x.com",
        )
        assert spawned.origin == "spawned"
        assert spawned.parent_member_id == member.id
        assert spawned.display_name == "Custom CTO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py -k instantiate_member -v`
Expected: FAIL with `ImportError: cannot import name 'instantiate_member'`

- [ ] **Step 3: Edit `core/workspace_factory.py`**

Add this function after `_default_guardrail_profile_id` and before `create_workspace`:

```python
def instantiate_member(
    session: Session,
    workspace_id: str,
    role_def: Dict,
    *,
    workspace_name: str = "",
    display_name: Optional[str] = None,
    system_prompt: Optional[str] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
    avatar_emoji: Optional[str] = None,
    order_index: int = 0,
    parent_member_id: Optional[str] = None,
    origin: str = "seed",
    guardrail_id: Optional[str] = None,
    owner_email: Optional[str] = None,
) -> WorkspaceMember:
    """Instantiate one catalog role into a real Agent + WorkspaceMember.

    Shared by create_workspace (origin="seed") and workspace_spawn (origin="spawned").
    `role_def` is any catalog dict from ROLE_CATALOG or SPAWN_ROLE_CATALOG.
    """
    display_name = (display_name or role_def["label"]).strip()
    description = f"{role_def['label']} of {workspace_name}" if workspace_name else role_def["label"]

    spec = AgentSpec(
        name=display_name,
        description=description,
        category=role_def["category"],
        system_prompt=system_prompt or role_def["default_system_prompt"],
        model_provider=model_provider or "anthropic",
        model_name=model_name or role_def["default_model"],
        temperature=0.7,
        max_tokens=2048,
        tools=list(role_def["suggested_tools"]),
        avatar_emoji=avatar_emoji or role_def["avatar_emoji"],
        crafted_mode="manual",
    )
    kwargs = spec.to_db_kwargs()
    kwargs["guardrail_profile_id"] = guardrail_id
    kwargs["owner_email"] = owner_email
    agent = Agent(**kwargs)
    session.add(agent)
    session.commit()
    session.refresh(agent)

    member = WorkspaceMember(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        agent_id=agent.id,
        role=role_def["id"],
        display_name=display_name,
        order_index=order_index,
        parent_member_id=parent_member_id,
        origin=origin,
        created_at=utcnow(),
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member
```

Then replace the body of the `for role_id in selected_roles:` loop in `create_workspace`
(the block from `ov = member_overrides.get(...)` through `order += 1`) with:

```python
        ov = member_overrides.get(role_id, {})
        instantiate_member(
            session, ws.id, role, workspace_name=ws.name,
            display_name=ov.get("display_name"),
            system_prompt=ov.get("system_prompt"),
            model_provider=ov.get("model_provider"),
            model_name=ov.get("model_name"),
            avatar_emoji=ov.get("avatar_emoji"),
            order_index=order,
            origin="seed",
            guardrail_id=guardrail_id,
            owner_email=owner_email,
        )
        order += 1
```

(Keep the `role = get_role(role_id)` / `if role is None: continue` lines above it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_spawn.py -k instantiate_member -v`
Expected: PASS

- [ ] **Step 5: Run the full suite (cycle-1 regression)**

Run: `pytest tests/ -v`
Expected: all PASS — especially `tests/test_workspaces.py` factory tests
(`test_create_workspace_builds_agents_members_and_seeds_mission`,
`test_create_workspace_applies_overrides`) prove the extract preserved behavior.

- [ ] **Step 6: Commit**

```bash
git add core/workspace_factory.py tests/test_workspace_spawn.py
git commit -m "refactor(workspace): extract instantiate_member shared by factory and spawn"
```

---

## Task 3: Spawn role catalog + spawn_member

**Files:**
- Create: `core/workspace_spawn.py`
- Test: `tests/test_workspace_spawn.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_spawn.py`:

```python
from core.agent_factory import CATEGORIES, TOOL_CATALOG
from core import workspace_spawn as ws_spawn
from core import workspace_factory as wf


def test_spawn_role_catalog_well_formed():
    assert {r["id"] for r in ws_spawn.SPAWN_ROLE_CATALOG} == {
        "project_manager", "developer", "auditor"}
    tool_ids = {t["id"] for t in TOOL_CATALOG}
    for r in ws_spawn.SPAWN_ROLE_CATALOG:
        assert r["label"] and r["avatar_emoji"]
        assert r["category"] in CATEGORIES
        assert len(r["default_system_prompt"]) > 40
        assert set(r["suggested_tools"]).issubset(tool_ids)


def test_spawn_member_creates_spawned_member(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        ceo = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        pm = ws_spawn.spawn_member(s, ws.id, "project_manager",
                                   parent_member_id=ceo.id, display_name="PM A")
        assert pm.role == "project_manager"
        assert pm.origin == "spawned"
        assert pm.parent_member_id == ceo.id
        assert pm.order_index == 1     # after the seed CEO at 0
        agent = s.get(Agent, pm.agent_id)
        assert agent is not None and agent.name == "PM A"


def test_spawn_member_unknown_role_raises(db):
    with Session(db) as s:
        s.add(Workspace(id="w9", owner_email="o@x.com", name="Acme"))
        s.commit()
        with pytest.raises(ValueError):
            ws_spawn.spawn_member(s, "w9", "wizard")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py -k "spawn_role or spawn_member" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.workspace_spawn'`

- [ ] **Step 3: Create `core/workspace_spawn.py`**

```python
"""CEO dynamic spawning: a project's team is created at runtime.

The CEO spawns a Project Manager per project; the PM spawns developers and an auditor
sized to the brief by a deterministic planner. Spawned members are ordinary Agent +
WorkspaceMember rows (via workspace_factory.instantiate_member) so they inherit run,
guardrails, chat, and observability. Team size is plan-capped — no runaway creation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlmodel import Session, select

from core.state import Workspace, WorkspaceMember, WorkspaceProject
from core.workspace_factory import _default_guardrail_profile_id, instantiate_member


SPAWN_ROLE_CATALOG: List[Dict] = [
    {
        "id": "project_manager",
        "label": "Project Manager",
        "avatar_emoji": "🗂️",
        "category": "ops",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["slack_post", "calendar", "github"],
        "default_system_prompt": (
            "You are a Project Manager spawned to deliver a specific project. You break "
            "the brief into tasks, coordinate the developers and auditor assigned to you, "
            "track progress, surface blockers, and keep delivery aligned with the brief "
            "and the company mission. Be concrete and outcome-focused."
        ),
    },
    {
        "id": "developer",
        "label": "Developer",
        "avatar_emoji": "💻",
        "category": "coding",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["github", "code_interpreter", "file_ops"],
        "default_system_prompt": (
            "You are a Developer spawned to implement assigned tasks for a project. You "
            "write correct, tested, maintainable code, ask for clarification when a task "
            "is ambiguous, and report what you changed. Prefer simple, working solutions."
        ),
    },
    {
        "id": "auditor",
        "label": "Auditor",
        "avatar_emoji": "🔍",
        "category": "coding",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["github", "code_interpreter", "sql_query"],
        "default_system_prompt": (
            "You are an Auditor spawned to review a project's work for quality, security, "
            "and compliance. You check correctness, flag risks and policy violations, and "
            "give clear, actionable findings. Be rigorous but fair."
        ),
    },
]


def get_spawn_role(role_id: str) -> Optional[Dict]:
    for r in SPAWN_ROLE_CATALOG:
        if r["id"] == role_id:
            return r
    return None


@dataclass
class TeamPlan:
    num_developers: int
    with_auditor: bool

    @property
    def team_size(self) -> int:
        # PM + developers + (auditor if present)
        return 1 + self.num_developers + (1 if self.with_auditor else 0)


def plan_team(brief: str) -> TeamPlan:
    """Deterministically size a delivery team from the brief length."""
    n = len(brief or "")
    if n < 120:
        num_developers = 1
    elif n < 400:
        num_developers = 2
    else:
        num_developers = 3
    # Safety-first: every project gets an auditor.
    return TeamPlan(num_developers=num_developers, with_auditor=True)


def _next_order_index(session: Session, workspace_id: str) -> int:
    rows = session.exec(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    ).all()
    return len(rows)


def spawn_member(session: Session, workspace_id: str, role_id: str, *,
                 parent_member_id: Optional[str] = None,
                 display_name: Optional[str] = None) -> WorkspaceMember:
    """Spawn one member of a spawnable role into the workspace."""
    role = get_spawn_role(role_id)
    if role is None:
        raise ValueError(f"unknown spawn role: {role_id}")
    ws = session.get(Workspace, workspace_id)
    guardrail_id = _default_guardrail_profile_id(session)
    return instantiate_member(
        session, workspace_id, role,
        workspace_name=ws.name if ws else "",
        display_name=display_name,
        order_index=_next_order_index(session, workspace_id),
        parent_member_id=parent_member_id,
        origin="spawned",
        guardrail_id=guardrail_id,
        owner_email=ws.owner_email if ws else None,
    )


def spawn_project_team(session: Session, workspace_id: str,
                       project: WorkspaceProject, *,
                       plan: Optional[TeamPlan] = None) -> Dict:
    """CEO → PM → developers/auditor. Sets project.pm_member_id; returns the team."""
    plan = plan or plan_team(project.brief)

    ceo = session.exec(
        select(WorkspaceMember).where(
            (WorkspaceMember.workspace_id == workspace_id)
            & (WorkspaceMember.role == "ceo")
            & (WorkspaceMember.origin == "seed")
        ).order_by(WorkspaceMember.order_index)
    ).first()
    ceo_id = ceo.id if ceo else None

    pm = spawn_member(session, workspace_id, "project_manager",
                      parent_member_id=ceo_id, display_name=f"PM · {project.name}")
    project.pm_member_id = pm.id
    session.add(project)
    session.commit()

    developers = [
        spawn_member(session, workspace_id, "developer", parent_member_id=pm.id,
                     display_name=f"Developer {i + 1} · {project.name}")
        for i in range(plan.num_developers)
    ]
    auditors = []
    if plan.with_auditor:
        auditors.append(spawn_member(session, workspace_id, "auditor",
                                     parent_member_id=pm.id,
                                     display_name=f"Auditor · {project.name}"))
    return {"pm": pm, "developers": developers, "auditors": auditors}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_spawn.py -k "spawn_role or spawn_member" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/workspace_spawn.py tests/test_workspace_spawn.py
git commit -m "feat(spawn): add spawn role catalog, spawn_member, plan_team, spawn_project_team"
```

---

## Task 4: Cost estimator

**Files:**
- Create: `core/cost_estimator.py`
- Test: `tests/test_workspace_spawn.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_spawn.py`:

```python
from core import cost_estimator as ce


def test_model_rates_known_and_fallback():
    in_rate, out_rate = ce.model_rates("anthropic", "claude-sonnet-4-6")
    assert in_rate == 3.0 and out_rate == 15.0
    # unknown provider/model → anthropic sonnet fallback
    assert ce.model_rates("nope", "nope") == (3.0, 15.0)


def test_estimate_is_deterministic(monkeypatch):
    monkeypatch.setenv("COST_ASSUMED_TURNS", "8")
    monkeypatch.setenv("COST_BASE_CONTEXT_TOKENS", "1500")
    monkeypatch.setenv("COST_OUTPUT_TOKENS_PER_TURN", "500")
    est = ce.estimate_project_cost("Build a landing page", team_size=4)
    # brief_tokens = len // 4 ; per_turn_in = 1500 + brief_tokens ; turns = 4*8 = 32
    brief_tokens = len("Build a landing page") // 4
    expected_in = (1500 + brief_tokens) * 32
    expected_out = 500 * 32
    assert est.input_tokens == expected_in
    assert est.output_tokens == expected_out
    assert est.team_size == 4
    # cost = in/1e6*3 + out/1e6*15
    expected_cost = round(expected_in / 1e6 * 3.0 + expected_out / 1e6 * 15.0, 4)
    assert est.cost_usd == expected_cost


def test_estimate_json_roundtrip():
    est = ce.estimate_project_cost("x" * 500, team_size=5)
    blob = est.to_json()
    back = ce.CostEstimate.from_json(blob)
    assert back.input_tokens == est.input_tokens
    assert back.cost_usd == est.cost_usd
    assert back.assumptions["assumed_turns"] == est.assumptions["assumed_turns"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py -k "model_rates or estimate" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.cost_estimator'`

- [ ] **Step 3: Create `core/cost_estimator.py`**

```python
"""Deterministic project cost estimator — the client-facing quote.

No LLM, no network. Estimates token usage from the brief size, the planned team size,
and an assumed number of turns, priced with real PROVIDER_CATALOG per-million-token
rates. Stored on WorkspaceProject and shown to the client at project initiation.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, Tuple

from core.llm_providers import PROVIDER_CATALOG

_FALLBACK_RATES: Tuple[float, float] = (3.0, 15.0)   # Anthropic Sonnet $/M tokens


def model_rates(provider: str, model: str) -> Tuple[float, float]:
    """Return (input_rate, output_rate) in USD per million tokens."""
    cat = PROVIDER_CATALOG.get(provider)
    if cat:
        for m in cat.get("models", []):
            if m["id"] == model:
                return float(m["in"]), float(m["out"])
    return _FALLBACK_RATES


@dataclass
class CostEstimate:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    team_size: int
    provider: str
    model: str
    assumptions: Dict

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(blob: str) -> "CostEstimate":
        return CostEstimate(**json.loads(blob))


def estimate_project_cost(brief: str, team_size: int, *,
                          provider: str = "anthropic",
                          model: str = "claude-sonnet-4-6",
                          assumed_turns: int = None) -> CostEstimate:
    """Deterministically estimate the cost of delivering `brief` with `team_size` agents."""
    if assumed_turns is None:
        assumed_turns = int(os.getenv("COST_ASSUMED_TURNS", "8"))
    base_ctx = int(os.getenv("COST_BASE_CONTEXT_TOKENS", "1500"))
    out_per_turn = int(os.getenv("COST_OUTPUT_TOKENS_PER_TURN", "500"))

    brief_tokens = len(brief or "") // 4
    total_turns = max(0, team_size) * assumed_turns
    input_tokens = (base_ctx + brief_tokens) * total_turns
    output_tokens = out_per_turn * total_turns

    in_rate, out_rate = model_rates(provider, model)
    cost_usd = round(input_tokens / 1e6 * in_rate + output_tokens / 1e6 * out_rate, 4)

    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        team_size=team_size,
        provider=provider,
        model=model,
        assumptions={
            "assumed_turns": assumed_turns,
            "base_context_tokens": base_ctx,
            "output_tokens_per_turn": out_per_turn,
            "brief_tokens": brief_tokens,
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_spawn.py -k "model_rates or estimate" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/cost_estimator.py tests/test_workspace_spawn.py
git commit -m "feat(spawn): add deterministic project cost estimator"
```

---

## Task 5: spawn_project_team integration test

**Files:**
- Test: `tests/test_workspace_spawn.py` (append) — exercises Task 3's `spawn_project_team` + `plan_team` end-to-end

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_spawn.py`:

```python
def test_plan_team_scales_with_brief():
    assert ws_spawn.plan_team("short").num_developers == 1
    assert ws_spawn.plan_team("x" * 200).num_developers == 2
    assert ws_spawn.plan_team("x" * 500).num_developers == 3
    assert ws_spawn.plan_team("anything").with_auditor is True


def test_spawn_project_team_builds_hierarchy(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        ceo = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        project = WorkspaceProject(id="p1", workspace_id=ws.id, name="Launch",
                                   brief="x" * 200)   # → 2 developers + auditor
        s.add(project)
        s.commit()

        team = ws_spawn.spawn_project_team(s, ws.id, project)
        assert team["pm"].role == "project_manager"
        assert team["pm"].parent_member_id == ceo.id
        assert len(team["developers"]) == 2
        assert len(team["auditors"]) == 1
        for dev in team["developers"] + team["auditors"]:
            assert dev.parent_member_id == team["pm"].id
            assert dev.origin == "spawned"
        assert s.get(WorkspaceProject, "p1").pm_member_id == team["pm"].id


def test_spawn_project_team_without_ceo_uses_none_parent(db):
    with Session(db) as s:
        # workspace with no CEO role
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="", selected_roles=["cfo"],
        )
        project = WorkspaceProject(id="p2", workspace_id=ws.id, name="X", brief="short")
        s.add(project)
        s.commit()
        team = ws_spawn.spawn_project_team(s, ws.id, project)
        assert team["pm"].parent_member_id is None
        assert len(team["developers"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py -k "plan_team or spawn_project_team" -v`
Expected: PASS already if Task 3 is correct — these test Task 3 code. If they FAIL, fix Task 3's `spawn_project_team`/`plan_team` to match (do not weaken the tests).

- [ ] **Step 3: (No new code expected)**

These tests exercise `plan_team` and `spawn_project_team` from Task 3. If they pass
immediately, good. If not, the defect is in Task 3 — fix it there.

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_workspace_spawn.py
git commit -m "test(spawn): cover plan_team scaling and spawn_project_team hierarchy"
```

---

## Task 6: Project routes + templates + detail link

**Files:**
- Modify: `dashboard/main.py`
- Create: `dashboard/templates/workspace_projects.html`, `workspace_project_new.html`, `workspace_project_detail.html`
- Modify: `dashboard/templates/workspace_detail.html`
- Test: `tests/test_workspace_spawn.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_spawn.py`:

```python
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db):
    import dashboard.main as dash
    return TestClient(dash.app)


def _ws_with_ceo(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        return ws.id


def test_project_create_flow_spawns_team_and_quotes(db, client):
    ws_id = _ws_with_ceo(db)
    r = client.get(f"/workspaces/{ws_id}/projects")
    assert r.status_code == 200

    r = client.post(f"/workspaces/{ws_id}/projects", data={
        "name": "Landing Page", "brief": "x" * 200, "client_name": "Globex",
    }, follow_redirects=False)
    assert r.status_code == 303
    detail_url = r.headers["location"]

    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "Landing Page" in detail.text
    assert "Globex" in detail.text
    assert "Project Manager" in detail.text or "PM ·" in detail.text

    with Session(db) as s:
        proj = s.exec(select(WorkspaceProject).where(
            WorkspaceProject.workspace_id == ws_id)).one()
        assert proj.status == "staffed"
        assert proj.estimate_json is not None
        # team spawned: PM + 2 devs + auditor under the workspace
        spawned = s.exec(select(WorkspaceMember).where(
            (WorkspaceMember.workspace_id == ws_id)
            & (WorkspaceMember.origin == "spawned"))).all()
        assert len(spawned) == 4


def test_project_create_missing_workspace_404(db, client):
    r = client.post("/workspaces/ghost/projects", data={"name": "X", "brief": "y"},
                    follow_redirects=False)
    assert r.status_code == 404


def test_project_archive(db, client):
    ws_id = _ws_with_ceo(db)
    r = client.post(f"/workspaces/{ws_id}/projects", data={"name": "P", "brief": "short"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/workspaces/{ws_id}/projects/{pid}/archive", follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        assert s.get(WorkspaceProject, pid).status == "archived"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_spawn.py -k "project_create or project_archive" -v`
Expected: FAIL with 404s (routes don't exist)

- [ ] **Step 3: Add imports + routes to `dashboard/main.py`**

Add near the other `core` imports:
```python
from core import workspace_spawn
from core.cost_estimator import estimate_project_cost, CostEstimate
```
Add `WorkspaceProject` to the `from core.state import (...)` block.

Add this block after the chat routes (`workspace_chat_pin`) and before the legacy project
routes:

```python
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
```

- [ ] **Step 4: Create `dashboard/templates/workspace_projects.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ ws.name }} · Projects{% endblock %}
{% block content %}
<section class="max-w-4xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}" class="text-xs text-slate-500 hover:text-white">← {{ ws.name }}</a>
  <div class="flex items-center justify-between mt-1 mb-6">
    <h1 class="text-2xl font-semibold text-white">Projects</h1>
    <a href="#new" class="text-sm font-medium bg-grad-brand text-ink-950 px-4 py-2 rounded-md shadow-glow-v">New project</a>
  </div>

  <div class="space-y-3 mb-10">
    {% for p in projects %}
      <a href="/workspaces/{{ ws.id }}/projects/{{ p.id }}" class="block border border-white/10 rounded-xl p-4 hover:border-violet-500/50 hover:bg-white/5 transition">
        <div class="flex items-center justify-between">
          <h2 class="font-semibold text-white">{{ p.name }}</h2>
          <span class="text-xs px-2 py-0.5 rounded-full bg-white/5 text-slate-300">{{ p.status }}</span>
        </div>
        {% if p.client_name %}<p class="text-slate-400 text-xs mt-1">Client: {{ p.client_name }}</p>{% endif %}
        {% if p.brief %}<p class="text-slate-400 text-sm mt-2 line-clamp-2">{{ p.brief }}</p>{% endif %}
      </a>
    {% else %}
      <p class="text-slate-500 text-sm">No projects yet. Open one below — the CEO will staff a team and quote the cost.</p>
    {% endfor %}
  </div>

  <h2 id="new" class="text-lg font-semibold text-white mb-3">New project</h2>
  <form method="post" action="/workspaces/{{ ws.id }}/projects" class="space-y-3">
    <input name="name" required placeholder="Project name" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <input name="client_name" placeholder="Client name (optional)" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <textarea name="brief" rows="3" placeholder="Brief: what needs to be built?" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white"></textarea>
    <button class="text-sm font-medium bg-grad-brand text-ink-950 px-5 py-2 rounded-md shadow-glow-v hover:shadow-glow-c transition">Open project & estimate</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 5: Create `dashboard/templates/workspace_project_new.html`**

(A standalone create page, in case it is linked directly. Same form as the list page.)

```html
{% extends "_base.html" %}
{% block title %}New project · {{ ws.name }}{% endblock %}
{% block content %}
<section class="max-w-2xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}/projects" class="text-xs text-slate-500 hover:text-white">← Projects</a>
  <h1 class="text-2xl font-semibold text-white mt-1 mb-6">New project</h1>
  <form method="post" action="/workspaces/{{ ws.id }}/projects" class="space-y-3">
    <input name="name" required placeholder="Project name" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <input name="client_name" placeholder="Client name (optional)" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <textarea name="brief" rows="4" placeholder="Brief: what needs to be built?" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white"></textarea>
    <button class="text-sm font-medium bg-grad-brand text-ink-950 px-5 py-2 rounded-md shadow-glow-v hover:shadow-glow-c transition">Open project & estimate</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 6: Create `dashboard/templates/workspace_project_detail.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ project.name }} · {{ ws.name }}{% endblock %}
{% block content %}
<section class="max-w-4xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}/projects" class="text-xs text-slate-500 hover:text-white">← Projects</a>
  <div class="flex items-start justify-between mt-1 mb-4">
    <div>
      <h1 class="text-2xl font-semibold text-white">{{ project.name }}</h1>
      {% if project.client_name %}<p class="text-slate-400 text-sm">Client: {{ project.client_name }}</p>{% endif %}
    </div>
    <div class="flex items-center gap-2">
      <span class="text-xs px-2 py-0.5 rounded-full bg-white/5 text-slate-300">{{ project.status }}</span>
      {% if project.status != 'archived' %}
        <form method="post" action="/workspaces/{{ ws.id }}/projects/{{ project.id }}/archive">
          <button class="text-xs text-slate-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white transition">Archive</button>
        </form>
      {% endif %}
    </div>
  </div>

  {% if project.brief %}
    <div class="border border-white/10 rounded-xl p-4 mb-6">
      <div class="text-xs uppercase tracking-wide text-slate-400 mb-1">Brief</div>
      <p class="text-slate-100 text-sm whitespace-pre-wrap">{{ project.brief }}</p>
    </div>
  {% endif %}

  {% if estimate %}
    <div class="border border-violet-500/30 bg-grad-soft rounded-xl p-5 mb-6">
      <div class="text-xs uppercase tracking-wide text-violet-300 mb-2">Client quote</div>
      <div class="text-3xl font-semibold text-white">${{ '%.2f'|format(estimate.cost_usd) }}</div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 text-sm">
        <div><div class="text-slate-500 text-xs">Team size</div>{{ estimate.team_size }} agents</div>
        <div><div class="text-slate-500 text-xs">Input tokens</div>{{ '{:,}'.format(estimate.input_tokens) }}</div>
        <div><div class="text-slate-500 text-xs">Output tokens</div>{{ '{:,}'.format(estimate.output_tokens) }}</div>
        <div><div class="text-slate-500 text-xs">Model</div>{{ estimate.model }}</div>
      </div>
      <p class="text-slate-500 text-xs mt-3">Assumes {{ estimate.assumptions.assumed_turns }} turns/agent · {{ estimate.provider }} pricing. Informational estimate.</p>
    </div>
  {% endif %}

  <h2 class="text-sm uppercase tracking-wide text-slate-400 mb-3">Spawned team</h2>
  {% if pm %}
    <div class="border border-white/10 rounded-lg p-3 mb-2">
      <a href="/agents/{{ pm.agent_id }}" class="flex items-center gap-3 hover:opacity-80">
        <span class="text-2xl">{{ agents[pm.agent_id].avatar_emoji if pm.agent_id in agents else '🗂️' }}</span>
        <div><div class="text-white text-sm font-medium">{{ pm.display_name }}</div>
          <div class="text-slate-500 text-xs uppercase tracking-wide">project manager</div></div>
      </a>
      <div class="mt-3 pl-6 space-y-2 border-l border-white/10">
        {% for m in reports %}
          <a href="/agents/{{ m.agent_id }}" class="flex items-center gap-3 hover:opacity-80">
            <span class="text-xl">{{ agents[m.agent_id].avatar_emoji if m.agent_id in agents else '🤖' }}</span>
            <div><div class="text-white text-sm">{{ m.display_name }}</div>
              <div class="text-slate-500 text-xs uppercase tracking-wide">{{ m.role }}</div></div>
          </a>
        {% endfor %}
      </div>
    </div>
  {% else %}
    <p class="text-slate-500 text-sm">No team spawned.</p>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 7: Add a "Projects" link in `dashboard/templates/workspace_detail.html`**

In the same flex `<div>` that holds the "Team chat →" link (added in cycle D), add a
Projects link right before the Team chat link:
```html
      <a href="/workspaces/{{ ws.id }}/projects" class="text-xs text-cyan-300 border border-cyan-500/30 px-3 py-1.5 rounded-md hover:bg-cyan-500/10 transition">Projects →</a>
```
(Place it as the first child of that `<div class="flex items-center gap-2">`, before the Team chat `<a>`.)

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_workspace_spawn.py -k "project_create or project_archive" -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add dashboard/main.py dashboard/templates/workspace_projects.html dashboard/templates/workspace_project_new.html dashboard/templates/workspace_project_detail.html dashboard/templates/workspace_detail.html tests/test_workspace_spawn.py
git commit -m "feat(spawn): add project routes, team-spawn flow, cost quote UI"
```

---

## Task 7: Config + docs

**Files:**
- Modify: `.env.example`, `CLAUDE.md`

- [ ] **Step 1: Document env vars in `.env.example`**

Append to `.env.example`:

```
# ── Project cost estimator (subsystem G) ─────────────────────
# Assumed LLM turns per agent when estimating a project's cost
COST_ASSUMED_TURNS=8
# Assumed input-context tokens per turn (before the brief)
COST_BASE_CONTEXT_TOKENS=1500
# Assumed output tokens per turn
COST_OUTPUT_TOKENS_PER_TURN=500
```

- [ ] **Step 2: Document the layer in `CLAUDE.md`**

Append these bullets to the END of item 6 ("Workspace layer") sub-list (3-space indent + `- `):

```markdown
   - `core/workspace_spawn.py` — CEO dynamic spawning (subsystem G): a `SPAWN_ROLE_CATALOG` (project_manager, developer, auditor); `spawn_member` instantiates one via the shared `workspace_factory.instantiate_member`; `plan_team` deterministically sizes a team from the brief; `spawn_project_team` builds CEO→PM→developers/auditor recorded via `WorkspaceMember.parent_member_id`/`origin`. Team size is plan-capped (no runaway spawning).
   - `core/cost_estimator.py` — deterministic client quote (`estimate_project_cost`) from brief size × team size × assumed turns, priced with real `PROVIDER_CATALOG` rates (`COST_*` env). Stored on `WorkspaceProject.estimate_json`. Routes: `/workspaces/{id}/projects` (+ create, `/projects/{pid}`, archive). Opening a project spawns the team, computes the quote, and seeds one memory `decision` entry.
```

- [ ] **Step 3: Run the full suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "chore(spawn): document cost env vars and the spawning/estimator layer"
```

---

## Done — definition of complete

- `pytest tests/ -v` green, including `tests/test_workspace_spawn.py`.
- Opening a project spawns CEO→PM→developers/auditor (recorded hierarchy) and stores a deterministic client quote shown on the project detail page.
- `create_workspace` seed behavior is byte-identical after the `instantiate_member` extract (cycle-1 tests pass).
- Spawning is deterministic and plan-capped; cost estimation is deterministic and offline.
