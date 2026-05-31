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
