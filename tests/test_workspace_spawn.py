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
