"""Tests for the voice marketing agent (subsystem F)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, MarketingContact, OutreachMessage,
    get_engine, init_db, utcnow,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'mkt.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("VOICE_BACKEND", "stub")
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_marketing_tables_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(MarketingContact(id="ct1", workspace_id="w1", name="Lee",
                               email="lee@globex.com", company="Globex"))
        s.add(OutreachMessage(id="om1", workspace_id="w1", contact_id="ct1",
                              subject="Hi", body="Hello", send_status="sent"))
        s.commit()
    with Session(db) as s:
        ct = s.get(MarketingContact, "ct1")
        om = s.get(OutreachMessage, "om1")
        assert ct.status == "lead" and ct.email == "lee@globex.com"
        assert om.kind == "outreach" and om.followup_count == 0
        assert om.next_followup_at is None
