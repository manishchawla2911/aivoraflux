# Workspace Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an owner create a Workspace ("AI company") of role-based agents drawn from a code-defined catalog, backed by a shared semantic memory (local ChromaDB), and run individual members with that shared memory injected into their context.

**Architecture:** A new Studio-side product layer on the existing FastAPI + SQLite app. `Workspace` + `WorkspaceMember` (a join table) reuse the existing `Agent` row for all behavior (run/guardrails/observability). Shared memory is canonical in a new `WorkspaceMemory` SQLite table and semantically indexed in a per-workspace ChromaDB collection; an embeddings abstraction (`stub`/`sentence-transformers`/`ollama`) supplies the vectors. The existing `/agents/{id}/run` path is extended: if an agent is a workspace member, relevant memory is prepended to its system prompt — otherwise it is byte-for-byte unchanged.

**Tech Stack:** Python 3, SQLModel/SQLite, FastAPI + Jinja2, ChromaDB (local persistent), pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-05-31-workspace-foundation-design.md`

**Conventions to honor:**
- Use `core.state.utcnow()`, never `datetime.utcnow()`.
- New SQLModel tables follow existing style: string PKs, JSON-in-text fields.
- ChromaDB / embeddings real-backend failures are **fail-open** (never break a run or a write), mirroring `core.observability.record_event` and `llm_providers._complete_ollama`.
- Tests run offline with no API keys: `EMBEDDING_BACKEND=stub`, completion monkeypatched. The `chromadb` import is guarded so the suite passes even if the lib is absent (SQL fallback path).
- After each task, run the full suite: `pytest tests/ -v`.

---

## File Structure

**New files**
- `core/workspace_roles.py` — `ROLE_CATALOG` constant + `get_role(role_id)` helper.
- `core/embeddings.py` — `embed(texts)` with `stub`/`sentence_transformers`/`ollama` backends.
- `core/workspace_memory.py` — `add_entry`, `query`, `build_memory_context`; ChromaDB + SQL fallback.
- `core/workspace_factory.py` — `create_workspace(...)`.
- `dashboard/templates/workspaces.html` — workspace list.
- `dashboard/templates/workspace_new.html` — creation wizard.
- `dashboard/templates/workspace_detail.html` — roster + mission + memory feed.
- `tests/test_workspaces.py` — all tests for this cycle.

**Modified files**
- `core/state.py` — add `Workspace`, `WorkspaceMember`, `WorkspaceMemory` tables.
- `dashboard/main.py` — workspace routes + memory-aware run extension.
- `dashboard/templates/_base.html` — add "Workspaces" nav link.
- `requirements.txt` — add `chromadb`.
- `.env.example` — document new env vars.
- `CLAUDE.md` — document the new Workspace layer.

---

## Task 1: Data model — Workspace, WorkspaceMember, WorkspaceMemory

**Files:**
- Modify: `core/state.py` (add three tables after the `GuardrailProfile` class, before the observability section)
- Test: `tests/test_workspaces.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspaces.py`:

```python
"""Tests for the Workspace foundation layer (workspaces, roles, memory)."""
from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceMemory,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh SQLite DB + stub embeddings + isolated Chroma dir per test."""
    db_url = f"sqlite:///{tmp_path / 'ws.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_workspace_tables_roundtrip(db):
    with Session(db) as s:
        ws = Workspace(id="w1", owner_email="o@x.com", name="Acme", mission="Win")
        agent = Agent(id="a1", name="Boss", system_prompt="You are CEO.")
        s.add(ws)
        s.add(agent)
        s.add(WorkspaceMember(
            id="m1", workspace_id="w1", agent_id="a1", role="ceo",
            display_name="Boss", order_index=0,
        ))
        s.add(WorkspaceMemory(
            id="e1", workspace_id="w1", author="owner", kind="goal", content="Win",
        ))
        s.commit()

    with Session(db) as s:
        members = s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == "w1")).all()
        mems = s.exec(select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == "w1")).all()
        assert len(members) == 1 and members[0].role == "ceo"
        assert len(mems) == 1 and mems[0].kind == "goal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py::test_workspace_tables_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'Workspace' from 'core.state'`

- [ ] **Step 3: Add the three tables**

In `core/state.py`, insert immediately **before** the line `# ─────...` that begins the
`# Observability` section (i.e. right after the `GuardrailProfile` class ends):

```python
# ─────────────────────────────────────────────────────────────
# Workspace layer — an "AI company": a roster of role agents that
# share a common memory. Members reuse the existing Agent row.
# ─────────────────────────────────────────────────────────────

class Workspace(SQLModel, table=True):
    """An owner's AI company — a named container for a roster + shared memory."""
    __tablename__ = "workspace"

    id: str = Field(primary_key=True)
    owner_email: Optional[str] = Field(default=None, index=True)
    name: str
    company_description: Optional[str] = None
    mission: Optional[str] = None
    status: str = "active"                      # active | archived
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkspaceMember(SQLModel, table=True):
    """Join row: places an existing Agent into a workspace under a role."""
    __tablename__ = "workspace_member"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    agent_id: str = Field(foreign_key="agent.id", index=True)
    role: str                                   # catalog role id, e.g. "ceo"
    display_name: Optional[str] = None
    order_index: int = 0
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceMemory(SQLModel, table=True):
    """One shared-memory entry. Canonical here; mirrored into ChromaDB by id."""
    __tablename__ = "workspace_memory"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    author: Optional[str] = None                # member display name or "owner"
    kind: str = "note"                          # goal | decision | fact | note
    content: str = ""
    tags: Optional[str] = None                  # JSON list
    created_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py::test_workspace_tables_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/state.py tests/test_workspaces.py
git commit -m "feat(workspace): add Workspace, WorkspaceMember, WorkspaceMemory tables"
```

---

## Task 2: Role catalog

**Files:**
- Create: `core/workspace_roles.py`
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
from core.agent_factory import CATEGORIES, TOOL_CATALOG
from core.workspace_roles import ROLE_CATALOG, get_role


def test_role_catalog_well_formed():
    assert {r["id"] for r in ROLE_CATALOG} == {"ceo", "cfo", "cto", "marketing", "coo"}
    tool_ids = {t["id"] for t in TOOL_CATALOG}
    for r in ROLE_CATALOG:
        assert r["label"] and r["avatar_emoji"]
        assert r["category"] in CATEGORIES
        assert len(r["default_system_prompt"]) > 40
        assert set(r["suggested_tools"]).issubset(tool_ids)
        assert r["default_model"]


def test_get_role_lookup():
    assert get_role("ceo")["label"] == "CEO"
    assert get_role("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py::test_role_catalog_well_formed -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.workspace_roles'`

- [ ] **Step 3: Create `core/workspace_roles.py`**

```python
"""Code-defined catalog of company roles, mirroring agent_factory.TOOL_CATALOG.

Each role is a template the owner instantiates into a real Agent when building a
workspace. Tool ids must exist in agent_factory.TOOL_CATALOG; categories must be
valid agent_factory.CATEGORIES values.
"""
from __future__ import annotations

from typing import Dict, List, Optional

ROLE_CATALOG: List[Dict] = [
    {
        "id": "ceo",
        "label": "CEO",
        "avatar_emoji": "👔",
        "category": "ops",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["web_search", "email_send", "slack_post"],
        "default_system_prompt": (
            "You are the CEO of this company. You set vision and strategy, make "
            "final calls when the team is split, and keep every decision tied to "
            "the workspace mission. Delegate execution detail to the CTO, CFO, and "
            "COO; you focus on priorities, trade-offs, and alignment. Be decisive "
            "and concise. When you decide something material, state it as a clear "
            "decision the team can record."
        ),
    },
    {
        "id": "cfo",
        "label": "CFO",
        "avatar_emoji": "💰",
        "category": "finance",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["sql_query", "web_search"],
        "default_system_prompt": (
            "You are the CFO. You own budgets, unit economics, runway, and cost "
            "discipline. Translate plans into numbers, flag financial risk early, "
            "and ground recommendations in concrete figures and assumptions. When "
            "data is missing, state the assumption you are using."
        ),
    },
    {
        "id": "cto",
        "label": "CTO",
        "avatar_emoji": "🛠️",
        "category": "coding",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["github", "web_search", "code_interpreter"],
        "default_system_prompt": (
            "You are the CTO. You own technical architecture, delivery, and "
            "engineering trade-offs. Recommend pragmatic, buildable approaches, "
            "call out technical risk, and keep solutions aligned to the mission "
            "and budget. Prefer simple designs; justify added complexity."
        ),
    },
    {
        "id": "marketing",
        "label": "Marketing Lead",
        "avatar_emoji": "📣",
        "category": "marketing",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["web_search", "email_send", "image_gen"],
        "default_system_prompt": (
            "You are the Marketing Lead. You own positioning, messaging, and "
            "go-to-market. Write clear, audience-aware copy, propose campaigns "
            "tied to goals, and keep brand voice consistent. (Voice and outbound "
            "email automation arrive in a later release; for now you draft and "
            "plan in text.)"
        ),
    },
    {
        "id": "coo",
        "label": "COO",
        "avatar_emoji": "📋",
        "category": "ops",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["slack_post", "calendar", "sql_query"],
        "default_system_prompt": (
            "You are the COO. You turn strategy into operating plans: process, "
            "staffing, timelines, and execution tracking. Keep the team unblocked "
            "and accountable, surface operational risk, and align day-to-day work "
            "with the mission."
        ),
    },
]


def get_role(role_id: str) -> Optional[Dict]:
    """Return the catalog entry for role_id, or None if unknown."""
    for r in ROLE_CATALOG:
        if r["id"] == role_id:
            return r
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py::test_role_catalog_well_formed tests/test_workspaces.py::test_get_role_lookup -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/workspace_roles.py tests/test_workspaces.py
git commit -m "feat(workspace): add code-defined role catalog (CEO/CFO/CTO/Marketing/COO)"
```

---

## Task 3: Embeddings abstraction

**Files:**
- Create: `core/embeddings.py`
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
from core import embeddings


def test_stub_embeddings_deterministic_and_dimensioned(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    a = embeddings.embed(["hello world", "different text"])
    b = embeddings.embed(["hello world", "different text"])
    assert a == b                                   # deterministic
    assert len(a) == 2
    assert all(len(v) == embeddings.STUB_DIM for v in a)
    assert a[0] != a[1]                             # distinct inputs differ


def test_embed_empty_returns_empty(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    assert embeddings.embed([]) == []


def test_unknown_backend_falls_back_to_stub(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "does-not-exist")
    out = embeddings.embed(["x"])
    assert len(out) == 1 and len(out[0]) == embeddings.STUB_DIM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py::test_stub_embeddings_deterministic_and_dimensioned -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.embeddings'`

- [ ] **Step 3: Create `core/embeddings.py`**

```python
"""Pluggable text-embedding seam, mirroring how llm_providers abstracts completion.

Backend selected by EMBEDDING_BACKEND:
  - "stub"                 deterministic hash vectors; no deps, no network (tests/offline)
  - "sentence_transformers" local all-MiniLM-L6-v2 (prod default; guarded import)
  - "ollama"               local Ollama embeddings endpoint

Any real-backend failure falls back to the stub with a logged warning, so a
missing model or daemon never breaks a memory write or a run.
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
from typing import List

logger = logging.getLogger(__name__)

STUB_DIM = 64
Vector = List[float]


def _stub_embed(texts: List[str]) -> List[Vector]:
    """Deterministic pseudo-embedding: hash → fixed-length float vector in [0,1)."""
    out: List[Vector] = []
    for t in texts:
        vec: Vector = []
        counter = 0
        # Expand the text hash until we have STUB_DIM floats.
        while len(vec) < STUB_DIM:
            h = hashlib.sha256(f"{t}#{counter}".encode("utf-8")).digest()
            for i in range(0, len(h), 4):
                if len(vec) >= STUB_DIM:
                    break
                (n,) = struct.unpack("I", h[i:i + 4])
                vec.append((n % 10_000) / 10_000.0)
            counter += 1
        out.append(vec)
    return out


def _sentence_transformers_embed(texts: List[str]) -> List[Vector]:
    from sentence_transformers import SentenceTransformer  # guarded import
    global _ST_MODEL
    try:
        _ST_MODEL
    except NameError:
        _ST_MODEL = SentenceTransformer(
            os.getenv("ST_EMBED_MODEL", "all-MiniLM-L6-v2")
        )
    return [list(map(float, v)) for v in _ST_MODEL.encode(texts)]


def _ollama_embed(texts: List[str]) -> List[Vector]:
    import json
    import urllib.request

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    out: List[Vector] = []
    for t in texts:
        payload = json.dumps({"model": model, "prompt": t}).encode()
        req = urllib.request.Request(
            f"{base}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        out.append([float(x) for x in body["embedding"]])
    return out


def embed(texts: List[str]) -> List[Vector]:
    """Embed a list of texts using the configured backend (fail-open to stub)."""
    if not texts:
        return []
    backend = os.getenv("EMBEDDING_BACKEND", "sentence_transformers")
    if backend == "stub":
        return _stub_embed(texts)
    try:
        if backend == "sentence_transformers":
            return _sentence_transformers_embed(texts)
        if backend == "ollama":
            return _ollama_embed(texts)
        logger.warning("embeddings.unknown_backend=%s — using stub", backend)
        return _stub_embed(texts)
    except Exception:
        logger.warning("embeddings.%s_failed — falling back to stub", backend, exc_info=True)
        return _stub_embed(texts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py -k embed -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/embeddings.py tests/test_workspaces.py
git commit -m "feat(workspace): add pluggable embeddings (stub/sentence-transformers/ollama)"
```

---

## Task 4: Memory store (ChromaDB + SQL fallback)

**Files:**
- Create: `core/workspace_memory.py`
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
from core import workspace_memory as wm


def _seed_ws(db, ws_id="w1", mission="Grow revenue 3x this year"):
    with Session(db) as s:
        s.add(Workspace(id=ws_id, owner_email="o@x.com", name="Acme", mission=mission))
        s.commit()


def test_add_entry_persists_to_sqlite(db):
    _seed_ws(db)
    with Session(db) as s:
        entry = wm.add_entry(s, "w1", author="owner", kind="fact",
                             content="We sell B2B analytics", tags=["context"])
        assert entry.id
        rows = s.exec(select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == "w1")).all()
        assert len(rows) == 1 and rows[0].content == "We sell B2B analytics"


def test_query_returns_relevant_entries(db):
    _seed_ws(db)
    with Session(db) as s:
        wm.add_entry(s, "w1", author="owner", kind="fact", content="Our pricing is usage-based")
        wm.add_entry(s, "w1", author="owner", kind="fact", content="Hiring two engineers in Q3")
        results = wm.query(s, "w1", "tell me about pricing", k=2)
        assert len(results) >= 1
        assert all(isinstance(r, WorkspaceMemory) for r in results)


def test_query_sql_fallback_when_chroma_unavailable(db, monkeypatch):
    _seed_ws(db)
    with Session(db) as s:
        wm.add_entry(s, "w1", author="owner", kind="note", content="alpha")
        wm.add_entry(s, "w1", author="owner", kind="note", content="beta")
        # Force the Chroma path to blow up — query must still return rows via SQL.
        monkeypatch.setattr(wm, "_get_collection", lambda ws_id: (_ for _ in ()).throw(RuntimeError("no chroma")))
        results = wm.query(s, "w1", "anything", k=5)
        assert len(results) == 2


def test_build_memory_context_includes_mission_and_entries(db):
    _seed_ws(db, mission="Become the #1 analytics tool")
    with Session(db) as s:
        wm.add_entry(s, "w1", author="cfo", kind="decision", content="Cap CAC at $400")
        ctx = wm.build_memory_context(s, "w1", "what is our budget stance?", k=5)
        assert "Become the #1 analytics tool" in ctx
        assert "Cap CAC at $400" in ctx
        assert "Shared workspace memory" in ctx


def test_build_memory_context_empty_workspace_is_safe(db):
    with Session(db) as s:
        # Unknown workspace → empty string, never raises.
        assert wm.build_memory_context(s, "ghost", "hi", k=5) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py -k "memory or query or context" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.workspace_memory'`

- [ ] **Step 3: Create `core/workspace_memory.py`**

```python
"""Shared workspace memory: canonical in SQLite, semantically indexed in ChromaDB.

SQLite (WorkspaceMemory) is the source of truth. Each entry id is mirrored as a
ChromaDB document (embedding + content + metadata) in a per-workspace collection
so agents can retrieve relevant memory by similarity. Every ChromaDB interaction
is fail-open: writes that fail are logged and dropped; queries that fail fall back
to recent-rows SQL filtering. Nothing here can break an agent run.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import List, Optional

from sqlmodel import Session, select

from core.embeddings import embed
from core.state import Workspace, WorkspaceMemory, utcnow

logger = logging.getLogger(__name__)


def _chroma_client():
    import chromadb  # guarded: absent lib → caller's try/except triggers SQL fallback
    path = os.getenv("CHROMA_DIR", "./chroma")
    return chromadb.PersistentClient(path=path)


def _collection_name(workspace_id: str) -> str:
    # Chroma names: 3-63 chars, alnum/._-, start & end alphanumeric.
    safe = "".join(c if c.isalnum() else "-" for c in workspace_id)
    return f"ws-{safe}"[:63].rstrip("-")


def _get_collection(workspace_id: str):
    client = _chroma_client()
    return client.get_or_create_collection(_collection_name(workspace_id))


def add_entry(
    session: Session,
    workspace_id: str,
    *,
    author: Optional[str],
    kind: str,
    content: str,
    tags: Optional[List[str]] = None,
) -> WorkspaceMemory:
    """Write a memory entry to SQLite, then mirror it into ChromaDB (fail-open)."""
    entry = WorkspaceMemory(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        author=author,
        kind=kind,
        content=content,
        tags=json.dumps(tags or []),
        created_at=utcnow(),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    try:
        vec = embed([content])
        _get_collection(workspace_id).add(
            ids=[entry.id],
            embeddings=vec,
            documents=[content],
            metadatas=[{"kind": kind, "author": author or "", "tags": entry.tags}],
        )
    except Exception:
        logger.warning("workspace_memory.chroma_add_failed ws=%s", workspace_id, exc_info=True)

    return entry


def _recent_rows(session: Session, workspace_id: str, k: int,
                 kind: Optional[str]) -> List[WorkspaceMemory]:
    stmt = select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == workspace_id)
    if kind:
        stmt = stmt.where(WorkspaceMemory.kind == kind)
    stmt = stmt.order_by(WorkspaceMemory.created_at.desc()).limit(k)
    return list(session.exec(stmt).all())


def query(
    session: Session,
    workspace_id: str,
    text: str,
    k: int = 5,
    kind: Optional[str] = None,
) -> List[WorkspaceMemory]:
    """Return up to k entries most relevant to `text` (semantic, else recent rows)."""
    try:
        qvec = embed([text])
        res = _get_collection(workspace_id).query(query_embeddings=qvec, n_results=k)
        ids = (res.get("ids") or [[]])[0]
        if not ids:
            return _recent_rows(session, workspace_id, k, kind)
        # Hydrate from SQLite, preserving Chroma's relevance order.
        rows = {
            r.id: r for r in session.exec(
                select(WorkspaceMemory).where(WorkspaceMemory.id.in_(ids))
            ).all()
        }
        ordered = [rows[i] for i in ids if i in rows]
        if kind:
            ordered = [r for r in ordered if r.kind == kind]
        return ordered or _recent_rows(session, workspace_id, k, kind)
    except Exception:
        logger.warning("workspace_memory.chroma_query_failed ws=%s — SQL fallback",
                       workspace_id, exc_info=True)
        return _recent_rows(session, workspace_id, k, kind)


def build_memory_context(session: Session, workspace_id: str, query_text: str,
                         k: int = 5) -> str:
    """Format mission + top-k relevant entries into a system-prompt memory block.

    Returns "" for an unknown/empty workspace so callers can prepend unconditionally.
    """
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        return ""
    entries = query(session, workspace_id, query_text, k=k)
    if ws.mission is None and not entries:
        return ""
    lines = ["## Shared workspace memory",
             "Use this shared context to stay aligned with the rest of the company."]
    if ws.mission:
        lines.append(f"Mission: {ws.mission}")
    for e in entries:
        author = e.author or "unknown"
        lines.append(f"- [{e.kind}] ({author}) {e.content}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py -k "memory or query or context" -v`
Expected: PASS (all memory tests)

Note: if `chromadb` is not installed in the environment, `test_query_returns_relevant_entries` still passes because the bare-`except` falls back to SQL recent rows. With `chromadb` installed (Task 8 adds it to requirements), it exercises the semantic path.

- [ ] **Step 5: Commit**

```bash
git add core/workspace_memory.py tests/test_workspaces.py
git commit -m "feat(workspace): add shared memory store (ChromaDB + SQL fallback)"
```

---

## Task 5: Workspace factory

**Files:**
- Create: `core/workspace_factory.py`
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
from core import workspace_factory as wf


def test_create_workspace_builds_agents_members_and_seeds_mission(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s,
            owner_email="o@x.com",
            name="Acme Co",
            company_description="B2B analytics",
            mission="Triple revenue",
            selected_roles=["ceo", "cto"],
        )
        agents = s.exec(select(Agent)).all()
        members = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).all()
        mems = s.exec(select(WorkspaceMemory).where(
            WorkspaceMemory.workspace_id == ws.id)).all()

        assert ws.status == "active"
        assert len(agents) == 2
        assert {m.role for m in members} == {"ceo", "cto"}
        # mission seeded as the first goal entry
        assert any(m.kind == "goal" and m.content == "Triple revenue" for m in mems)
        # order_index assigned in selection order
        assert sorted(m.order_index for m in members) == [0, 1]


def test_create_workspace_applies_overrides(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s,
            owner_email="o@x.com",
            name="Acme",
            company_description="",
            mission="",
            selected_roles=["ceo"],
            member_overrides={"ceo": {"display_name": "Dana", "system_prompt": "Custom CEO."}},
        )
        member = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        agent = s.get(Agent, member.agent_id)
        assert member.display_name == "Dana"
        assert agent.name == "Dana"
        assert agent.system_prompt == "Custom CEO."


def test_create_workspace_ignores_unknown_roles(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme",
            company_description="", mission="", selected_roles=["ceo", "wizard"],
        )
        members = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).all()
        assert {m.role for m in members} == {"ceo"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py -k create_workspace -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.workspace_factory'`

- [ ] **Step 3: Create `core/workspace_factory.py`**

```python
"""Build a workspace: instantiate role templates into real Agents + members.

Pure functions over a passed Session (mirrors agent_factory's separation from the
web layer). Reuses agent_factory.AgentSpec.to_db_kwargs so workspace agents are
identical in shape to Studio agents and inherit every downstream capability.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from sqlmodel import Session, select

from core.agent_factory import AgentSpec
from core.state import Agent, GuardrailProfile, Workspace, WorkspaceMember, utcnow
from core.workspace_roles import get_role
from core import workspace_memory


def _default_guardrail_profile_id(session: Session) -> str:
    """Return (creating if needed) the 'Standard' guardrail profile id.

    Mirrors dashboard.main._default_guardrail_profile so factory-built agents are
    guardrailed exactly like Studio-built ones, without importing the web module.
    """
    gp = session.exec(
        select(GuardrailProfile).where(GuardrailProfile.name == "Standard")
    ).first()
    if gp is None:
        gp = GuardrailProfile(id=str(uuid.uuid4()), name="Standard",
                              description="Default — all SOTA layers active.")
        session.add(gp)
        session.commit()
        session.refresh(gp)
    return gp.id


def create_workspace(
    session: Session,
    *,
    owner_email: Optional[str],
    name: str,
    company_description: str,
    mission: str,
    selected_roles: List[str],
    member_overrides: Optional[Dict[str, Dict]] = None,
) -> Workspace:
    """Create a workspace, instantiate selected roles as Agents+members, seed mission."""
    member_overrides = member_overrides or {}

    ws = Workspace(
        id=str(uuid.uuid4()),
        owner_email=owner_email,
        name=name.strip() or "Untitled Workspace",
        company_description=(company_description or "").strip() or None,
        mission=(mission or "").strip() or None,
        status="active",
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)

    guardrail_id = _default_guardrail_profile_id(session)

    order = 0
    for role_id in selected_roles:
        role = get_role(role_id)
        if role is None:
            continue
        ov = member_overrides.get(role_id, {})
        display_name = (ov.get("display_name") or role["label"]).strip()

        spec = AgentSpec(
            name=display_name,
            description=f"{role['label']} of {ws.name}",
            category=role["category"],
            system_prompt=ov.get("system_prompt") or role["default_system_prompt"],
            model_provider=ov.get("model_provider") or "anthropic",
            model_name=ov.get("model_name") or role["default_model"],
            temperature=0.7,
            max_tokens=2048,
            tools=list(role["suggested_tools"]),
            avatar_emoji=ov.get("avatar_emoji") or role["avatar_emoji"],
            crafted_mode="manual",
        )
        kwargs = spec.to_db_kwargs()
        kwargs["guardrail_profile_id"] = guardrail_id
        kwargs["owner_email"] = owner_email
        agent = Agent(**kwargs)
        session.add(agent)
        session.commit()
        session.refresh(agent)

        session.add(WorkspaceMember(
            id=str(uuid.uuid4()),
            workspace_id=ws.id,
            agent_id=agent.id,
            role=role_id,
            display_name=display_name,
            order_index=order,
            created_at=utcnow(),
        ))
        session.commit()
        order += 1

    if ws.mission:
        workspace_memory.add_entry(
            session, ws.id, author="owner", kind="goal",
            content=ws.mission, tags=["mission"],
        )

    return ws
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py -k create_workspace -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/workspace_factory.py tests/test_workspaces.py
git commit -m "feat(workspace): add workspace factory (roles → agents + members + mission)"
```

---

## Task 6: Memory-aware run extension

**Files:**
- Modify: `dashboard/main.py` (imports + `run_agent`)
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
from fastapi.testclient import TestClient

from core.llm_providers import CompletionResponse


@pytest.fixture()
def client(db, monkeypatch):
    """TestClient sharing the `db` fixture's engine + a completion that echoes
    its received system prompt, so we can assert what the agent was given."""
    import dashboard.main as dash

    def fake_complete(req, provider):
        return CompletionResponse(text=f"SYSTEM_WAS:::{req.system}",
                                  provider=provider, model=req.model, stubbed=True)

    monkeypatch.setattr(dash, "complete", fake_complete)
    return TestClient(dash.app)


def test_workspace_member_run_injects_memory(db, client):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Dominate the analytics market", selected_roles=["ceo"],
        )
        member = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        agent_id = member.agent_id

    r = client.post(f"/agents/{agent_id}/run", data={"input": "what is our mission?"})
    assert r.status_code == 200
    out = r.json()["output"]
    assert "Shared workspace memory" in out                 # block was prepended
    assert "Dominate the analytics market" in out            # mission included
    assert "You are the CEO" in out                          # original prompt kept


def test_non_workspace_agent_run_is_unchanged(db, client):
    with Session(db) as s:
        s.add(Agent(id="solo", name="Solo", system_prompt="You are solo."))
        s.commit()

    r = client.post("/agents/solo/run", data={"input": "hi"})
    assert r.status_code == 200
    out = r.json()["output"]
    assert "Shared workspace memory" not in out
    assert out == "SYSTEM_WAS:::You are solo."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py -k "member_run or non_workspace" -v`
Expected: FAIL — `test_workspace_member_run_injects_memory` fails because the memory block is not in the output (run path not yet memory-aware).

- [ ] **Step 3: Extend `run_agent` in `dashboard/main.py`**

First, add to the imports near the other `core.state` import (the existing block is
`from core.state import (Agent, AgentRun, GuardrailProfile, HumanDecision, Project, ProviderConfig, get_engine, init_db, utcnow,)`):

```python
from core.state import (
    Agent, AgentRun, GuardrailProfile, HumanDecision, Project,
    ProviderConfig, Workspace, WorkspaceMember, WorkspaceMemory,
    get_engine, init_db, utcnow,
)
from core import workspace_memory
from core.workspace_factory import create_workspace
from core.workspace_roles import ROLE_CATALOG
```

Then add this env constant next to `MAX_RUN_INPUT_CHARS` (~line 65):

```python
WORKSPACE_MEMORY_K = int(os.getenv("WORKSPACE_MEMORY_K", "5"))
```

Then, inside `run_agent`, replace the existing session block that loads the agent +
profile (the `with Session(get_engine()) as s:` block ending at the `profile = ...`
line) with one that also resolves the workspace memory context:

```python
    memory_block = ""
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        profile = s.get(GuardrailProfile, agent.guardrail_profile_id) if agent.guardrail_profile_id else None
        member = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.agent_id == agent_id)
        ).first()
        if member:
            memory_block = workspace_memory.build_memory_context(
                s, member.workspace_id, input, k=WORKSPACE_MEMORY_K
            )
```

Finally, change the `CompletionRequest` construction so the memory block is prepended
to the system prompt when present:

```python
    system_prompt = agent.system_prompt
    if memory_block:
        system_prompt = f"{memory_block}\n\n{agent.system_prompt}"

    req = CompletionRequest(
        system=system_prompt,
        user=input,
        model=agent.model_name,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py -k "member_run or non_workspace" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite (no regressions in existing run path)**

Run: `pytest tests/ -v`
Expected: PASS (all tests, including the existing `test_dashboard.py` / `test_agent_studio.py`)

- [ ] **Step 6: Commit**

```bash
git add dashboard/main.py tests/test_workspaces.py
git commit -m "feat(workspace): make agent runs memory-aware for workspace members"
```

---

## Task 7: Workspace routes + templates + nav

**Files:**
- Modify: `dashboard/main.py` (add routes)
- Create: `dashboard/templates/workspaces.html`, `workspace_new.html`, `workspace_detail.html`
- Modify: `dashboard/templates/_base.html` (nav link)
- Test: `tests/test_workspaces.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspaces.py`:

```python
def test_workspace_create_flow_via_http(db, client):
    # empty list first
    r = client.get("/workspaces")
    assert r.status_code == 200

    # wizard renders the role catalog
    r = client.get("/workspaces/new")
    assert r.status_code == 200
    assert "CEO" in r.text and "CFO" in r.text

    # create
    r = client.post("/workspaces", data={
        "name": "Acme Co",
        "company_description": "B2B analytics",
        "mission": "Triple revenue this year",
        "roles": ["ceo", "cfo"],
    }, follow_redirects=False)
    assert r.status_code == 303
    detail_url = r.headers["location"]

    # detail shows roster + mission
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "Acme Co" in detail.text
    assert "Triple revenue this year" in detail.text
    assert "CEO" in detail.text and "CFO" in detail.text


def test_add_memory_entry_via_http(db, client):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        ws_id = ws.id

    r = client.post(f"/workspaces/{ws_id}/memory", data={
        "kind": "decision", "content": "Launch in Q4", "tags": "launch,strategy",
    }, follow_redirects=False)
    assert r.status_code == 303

    with Session(db) as s:
        rows = s.exec(select(WorkspaceMemory).where(
            WorkspaceMemory.workspace_id == ws_id)).all()
        assert any(x.content == "Launch in Q4" and x.kind == "decision" for x in rows)


def test_archive_workspace_via_http(db, client):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="", selected_roles=["ceo"],
        )
        ws_id = ws.id

    r = client.post(f"/workspaces/{ws_id}/archive", follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        assert s.get(Workspace, ws_id).status == "archived"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspaces.py -k "create_flow or add_memory or archive_workspace" -v`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Add routes to `dashboard/main.py`**

Add this block after the agent run/`_save_run` section (before the legacy project
routes). It reuses `select`, `Session`, `get_engine`, `utcnow`, `json`, already imported:

```python
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
```

- [ ] **Step 4: Create `dashboard/templates/workspaces.html`**

```html
{% extends "_base.html" %}
{% block title %}Workspaces — Agent Studio{% endblock %}
{% block content %}
<section class="max-w-5xl mx-auto px-6 lg:px-8 py-12">
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-2xl font-semibold text-white">Workspaces</h1>
      <p class="text-slate-400 text-sm mt-1">Each workspace is an AI company — a roster of role agents that share memory.</p>
    </div>
    <a href="/workspaces/new" class="inline-flex items-center gap-1.5 text-sm font-medium bg-grad-brand text-ink-950 px-4 py-2 rounded-md shadow-glow-v hover:shadow-glow-c transition">New workspace</a>
  </div>

  {% if not workspaces %}
    <div class="border border-white/10 rounded-xl p-10 text-center text-slate-400">
      No workspaces yet. <a href="/workspaces/new" class="text-violet-400 hover:text-white">Create your first AI company →</a>
    </div>
  {% else %}
    <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
      {% for w in workspaces %}
        <a href="/workspaces/{{ w.id }}" class="block border border-white/10 rounded-xl p-5 hover:border-violet-500/50 hover:bg-white/5 transition">
          <div class="flex items-center justify-between">
            <h2 class="font-semibold text-white">{{ w.name }}</h2>
            <span class="text-xs px-2 py-0.5 rounded-full {{ 'bg-emerald-500/15 text-emerald-300' if w.status == 'active' else 'bg-slate-500/15 text-slate-400' }}">{{ w.status }}</span>
          </div>
          {% if w.mission %}<p class="text-slate-400 text-sm mt-2 line-clamp-2">{{ w.mission }}</p>{% endif %}
          <p class="text-slate-500 text-xs mt-4">{{ member_counts[w.id] }} member(s)</p>
        </a>
      {% endfor %}
    </div>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 5: Create `dashboard/templates/workspace_new.html`**

```html
{% extends "_base.html" %}
{% block title %}New workspace — Agent Studio{% endblock %}
{% block content %}
<section class="max-w-3xl mx-auto px-6 lg:px-8 py-12">
  <h1 class="text-2xl font-semibold text-white mb-1">New workspace</h1>
  <p class="text-slate-400 text-sm mb-8">Name your company, set a mission, and pick the roles to staff it. You can customize each agent's name now and refine prompts later.</p>

  <form method="post" action="/workspaces" class="space-y-6">
    <div>
      <label class="block text-sm text-slate-300 mb-1">Company name</label>
      <input name="name" required class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white" placeholder="Acme Analytics">
    </div>
    <div>
      <label class="block text-sm text-slate-300 mb-1">Description</label>
      <input name="company_description" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white" placeholder="B2B analytics for mid-market SaaS">
    </div>
    <div>
      <label class="block text-sm text-slate-300 mb-1">Mission</label>
      <textarea name="mission" rows="2" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white" placeholder="Become the #1 analytics tool for SaaS by 2027"></textarea>
      <p class="text-xs text-slate-500 mt-1">Seeded as the first shared-memory entry so every agent stays aligned.</p>
    </div>

    <div>
      <label class="block text-sm text-slate-300 mb-3">Roles</label>
      <div class="grid sm:grid-cols-2 gap-3">
        {% for r in roles %}
          <label class="flex items-start gap-3 border border-white/10 rounded-lg p-3 hover:border-violet-500/50 transition cursor-pointer">
            <input type="checkbox" name="roles" value="{{ r.id }}" class="mt-1" checked>
            <div class="flex-1">
              <div class="text-white text-sm font-medium">{{ r.avatar_emoji }} {{ r.label }}</div>
              <input name="name_{{ r.id }}" placeholder="Custom name (optional)" class="mt-2 w-full bg-ink-900 border border-white/10 rounded px-2 py-1 text-xs text-white">
            </div>
          </label>
        {% endfor %}
      </div>
    </div>

    <button type="submit" class="inline-flex items-center gap-1.5 text-sm font-medium bg-grad-brand text-ink-950 px-5 py-2.5 rounded-md shadow-glow-v hover:shadow-glow-c transition">Create workspace</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 6: Create `dashboard/templates/workspace_detail.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ ws.name }} — Workspace{% endblock %}
{% block content %}
<section class="max-w-5xl mx-auto px-6 lg:px-8 py-12">
  <div class="flex items-start justify-between mb-2">
    <div>
      <a href="/workspaces" class="text-xs text-slate-500 hover:text-white">← Workspaces</a>
      <h1 class="text-2xl font-semibold text-white mt-1">{{ ws.name }}</h1>
      {% if ws.company_description %}<p class="text-slate-400 text-sm">{{ ws.company_description }}</p>{% endif %}
    </div>
    {% if ws.status == 'active' %}
      <form method="post" action="/workspaces/{{ ws.id }}/archive">
        <button class="text-xs text-slate-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition">Archive</button>
      </form>
    {% endif %}
  </div>

  {% if ws.mission %}
    <div class="border border-violet-500/30 bg-grad-soft rounded-xl p-4 my-6">
      <div class="text-xs uppercase tracking-wide text-violet-300 mb-1">Mission</div>
      <p class="text-white">{{ ws.mission }}</p>
    </div>
  {% endif %}

  <div class="grid lg:grid-cols-2 gap-8 mt-8">
    <!-- Roster -->
    <div>
      <h2 class="text-sm uppercase tracking-wide text-slate-400 mb-3">Roster</h2>
      <div class="space-y-3">
        {% for m in members %}
          {% set a = agents.get(m.agent_id) %}
          <a href="/agents/{{ m.agent_id }}" class="flex items-center gap-3 border border-white/10 rounded-lg p-3 hover:border-violet-500/50 hover:bg-white/5 transition">
            <span class="text-2xl">{{ a.avatar_emoji if a else '🤖' }}</span>
            <div class="flex-1">
              <div class="text-white text-sm font-medium">{{ m.display_name or (a.name if a else m.role) }}</div>
              <div class="text-slate-500 text-xs uppercase tracking-wide">{{ m.role }}</div>
            </div>
            <span class="text-xs text-violet-400">Open →</span>
          </a>
        {% endfor %}
      </div>
    </div>

    <!-- Shared memory -->
    <div>
      <h2 class="text-sm uppercase tracking-wide text-slate-400 mb-3">Shared memory</h2>
      <form method="post" action="/workspaces/{{ ws.id }}/memory" class="space-y-2 mb-5">
        <div class="flex gap-2">
          <select name="kind" class="bg-ink-800 border border-white/10 rounded-md px-2 py-2 text-sm text-white">
            {% for k in memory_kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}
          </select>
          <input name="tags" placeholder="tags (comma-sep)" class="flex-1 bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white">
        </div>
        <textarea name="content" rows="2" required placeholder="Add a fact, decision, or note the whole team should know…" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white"></textarea>
        <button class="text-sm font-medium bg-grad-brand text-ink-950 px-4 py-1.5 rounded-md">Add to memory</button>
      </form>

      <div class="space-y-2 max-h-96 overflow-y-auto scrollbar-thin">
        {% for e in memories %}
          <div class="border border-white/10 rounded-lg p-3">
            <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
              <span class="px-1.5 py-0.5 rounded bg-white/5 text-slate-300">{{ e.kind }}</span>
              <span>{{ e.author or 'unknown' }}</span>
            </div>
            <p class="text-slate-200 text-sm">{{ e.content }}</p>
          </div>
        {% else %}
          <p class="text-slate-500 text-sm">No memory entries yet.</p>
        {% endfor %}
      </div>
    </div>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 7: Add the nav link in `dashboard/templates/_base.html`**

In the `<nav>` block (the `hidden md:flex` one), add a Workspaces link after the
Dashboard link:

```html
        <a href="/dashboard" class="hover:text-white transition">Dashboard</a>
        <a href="/workspaces" class="hover:text-white transition">Workspaces</a>
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_workspaces.py -k "create_flow or add_memory or archive_workspace" -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS (all)

- [ ] **Step 10: Commit**

```bash
git add dashboard/main.py dashboard/templates/workspaces.html dashboard/templates/workspace_new.html dashboard/templates/workspace_detail.html dashboard/templates/_base.html tests/test_workspaces.py
git commit -m "feat(workspace): add workspace list/new/detail routes, templates, and nav"
```

---

## Task 8: Config, dependencies, and docs

**Files:**
- Modify: `requirements.txt`, `.env.example`, `CLAUDE.md`

- [ ] **Step 1: Add ChromaDB to `requirements.txt`**

Append (keep alphabetical/grouped with existing entries; `sentence-transformers`
stays optional and is intentionally NOT added — its import is guarded):

```
chromadb>=0.5
```

- [ ] **Step 2: Install and verify import**

Run: `pip install -r requirements.txt`
Then run: `python -c "import chromadb; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Document env vars in `.env.example`**

Append:

```
# ── Workspace shared memory ──────────────────────────────────
# Embedding backend for workspace memory: stub | sentence_transformers | ollama
EMBEDDING_BACKEND=sentence_transformers
# Where ChromaDB persists its per-workspace collections
CHROMA_DIR=./chroma
# Ollama embedding model (when EMBEDDING_BACKEND=ollama)
OLLAMA_EMBED_MODEL=nomic-embed-text
# How many shared-memory entries to inject into a member's context per run
WORKSPACE_MEMORY_K=5
```

- [ ] **Step 4: Document the layer in `CLAUDE.md`**

Add a new bullet to the architecture section, after the "Agent Studio layer" (item 5)
description:

```markdown
6. **Workspace layer** — a third product on the same app/DB: an "AI company".
   - `core/state.py` hosts `Workspace`, `WorkspaceMember` (join → existing `Agent`), and `WorkspaceMemory`.
   - `core/workspace_roles.py` — `ROLE_CATALOG` of code-defined role templates (CEO, CFO, CTO, Marketing, COO), mirroring `TOOL_CATALOG`.
   - `core/workspace_factory.py::create_workspace` — instantiates selected roles into real `Agent` rows + `WorkspaceMember` links and seeds the mission as the first memory entry.
   - `core/workspace_memory.py` — shared memory store. Canonical in SQLite (`WorkspaceMemory`); semantically indexed in a per-workspace **ChromaDB** collection under `CHROMA_DIR`. `add_entry`/`query`/`build_memory_context`; **fail-open** — Chroma failures fall back to SQL recent-rows and never break a run.
   - `core/embeddings.py` — pluggable `embed()` (`EMBEDDING_BACKEND`: `stub` for tests, `sentence_transformers` default, `ollama`), fail-open to the stub.
   - Routes: `/workspaces`, `/workspaces/new`, `/workspaces/{id}`, plus memory-add and archive. Member runs reuse `/agents/{id}/run`, which prepends the shared-memory block to the system prompt **only** when the agent is a workspace member (Studio runs are unchanged).
```

- [ ] **Step 5: Run the full suite one final time**

Run: `pytest tests/ -v`
Expected: PASS (all, including the semantic ChromaDB path now that the lib is installed)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example CLAUDE.md
git commit -m "chore(workspace): add chromadb dep, env vars, and CLAUDE.md docs"
```

---

## Done — definition of complete

- `pytest tests/ -v` green, including `tests/test_workspaces.py`.
- `/workspaces/new` creates a workspace; its roster links to working `/agents/{id}` run consoles.
- Running a workspace member visibly grounds answers in the shared mission/memory; running a non-workspace Studio agent is unchanged.
- Adding a memory entry persists to SQLite and (when ChromaDB is present) becomes semantically retrievable.
- All ChromaDB/embedding failures degrade gracefully (SQL fallback / stub), never breaking a run.
