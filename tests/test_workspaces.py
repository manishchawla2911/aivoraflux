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
