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

    monkeypatch.setattr("core.llm_providers.complete", fake_complete)
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
