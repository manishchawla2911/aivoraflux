"""Tests for the external chat bridge (subsystem E)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceChannel,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'bridge.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_channel_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(WorkspaceChannel(
            id="c1", workspace_id="w1", platform="telegram",
            external_id="12345", label="Founders", token_env="TELEGRAM_BOT_TOKEN",
        ))
        s.commit()
    with Session(db) as s:
        ch = s.exec(select(WorkspaceChannel).where(
            WorkspaceChannel.platform == "telegram")).one()
        assert ch.external_id == "12345"
        assert ch.active is True
