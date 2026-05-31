"""Tests for internal agent chat (subsystem D)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceMemory, WorkspaceChatMessage,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'chat.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_chat_message_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(WorkspaceChatMessage(
            id="c1", workspace_id="w1", author_kind="owner",
            author_name="Owner", content="hello @CEO", mentions='["m1"]',
        ))
        s.commit()
    with Session(db) as s:
        rows = s.exec(select(WorkspaceChatMessage).where(
            WorkspaceChatMessage.workspace_id == "w1")).all()
        assert len(rows) == 1
        assert rows[0].author_kind == "owner"
        assert rows[0].triggered_by_id is None
        assert rows[0].pinned_memory_id is None


from core import workspace_factory as wf
from core.agent_runtime import run_agent_completion
from core.llm_providers import CompletionResponse


def _echo_complete(req, provider):
    return CompletionResponse(text=f"SYS:::{req.system}", provider=provider,
                              model=req.model, stubbed=True)


def test_run_agent_completion_passes_and_persists(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    with Session(db) as s:
        s.add(Agent(id="a1", name="Solo", system_prompt="You are solo.",
                    model_name="m", model_provider="anthropic"))
        s.commit()
        agent = s.get(Agent, "a1")
        result = run_agent_completion(s, agent, user_input="hi", extra_system="MEMCTX")
        assert result.blocked is False
        assert result.verdict == "passed"
        assert result.final_text == "SYS:::MEMCTX\n\nYou are solo."
        from core.state import AgentRun
        runs = s.exec(select(AgentRun).where(AgentRun.agent_id == "a1")).all()
        assert len(runs) == 1 and runs[0].guardrail_verdict == "passed"


def test_run_agent_completion_no_extra_system(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    with Session(db) as s:
        s.add(Agent(id="a2", name="Solo", system_prompt="Bare.",
                    model_name="m", model_provider="anthropic"))
        s.commit()
        agent = s.get(Agent, "a2")
        result = run_agent_completion(s, agent, user_input="hi")
        assert result.final_text == "SYS:::Bare."


from core import workspace_chat as wc


def test_resolve_mentions_basic():
    members = [("m1", "CEO", "ceo"), ("m2", "CFO", "cfo")]
    assert wc.resolve_mentions("hey @CEO what now", members) == ["m1"]
    assert wc.resolve_mentions("ping /cfo please", members) == ["m2"]


def test_resolve_mentions_longest_match_wins():
    members = [("m1", "Marketing", "marketing"),
               ("m2", "Marketing Lead", "marketing-lead")]
    assert wc.resolve_mentions("talk to @Marketing Lead now", members) == ["m2"]


def test_resolve_mentions_unknown_ignored_and_dedup():
    members = [("m1", "CEO", "ceo")]
    assert wc.resolve_mentions("@nobody here", members) == []
    assert wc.resolve_mentions("@CEO and again @ceo", members) == ["m1"]


def test_resolve_mentions_order_preserved():
    members = [("m1", "CEO", "ceo"), ("m2", "CFO", "cfo")]
    assert wc.resolve_mentions("@CFO then @CEO", members) == ["m2", "m1"]


def _make_ws(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win the market", selected_roles=["ceo", "cfo"],
        )
        return ws.id


def test_owner_post_no_mention_creates_only_owner_message(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="just thinking")
        assert len(created) == 1
        assert created[0].author_kind == "owner"
        agent_msgs = s.exec(select(WorkspaceChatMessage).where(
            WorkspaceChatMessage.author_kind == "agent")).all()
        assert agent_msgs == []


def test_single_mention_triggers_one_reply_grounded(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="hey @CEO plan?")
        assert len(created) == 2
        reply = created[1]
        assert reply.author_kind == "agent"
        assert reply.triggered_by_id == created[0].id
        assert "Win the market" in reply.content
        assert "Team chat (recent)" in reply.content


def test_cascade_is_bounded_and_loops_guarded(db, monkeypatch):
    def chain_complete(req, provider):
        return CompletionResponse(text="@CFO @CEO keep going", provider=provider,
                                  model=req.model, stubbed=True)
    monkeypatch.setattr("core.llm_providers.complete", chain_complete)
    monkeypatch.setenv("WORKSPACE_CHAT_MAX_TURNS", "6")
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="@CEO start")
        agent_turns = [m for m in created if m.author_kind == "agent"]
        assert len(agent_turns) <= 6
        triggered_members = [m.author_member_id for m in agent_turns]
        assert len(triggered_members) == len(set(triggered_members))  # no member twice
