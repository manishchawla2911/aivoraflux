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
