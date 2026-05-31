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
                                   brief="x" * 200)   # 2 developers + auditor
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
