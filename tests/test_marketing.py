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


from core import voice


def test_voice_stub_synthesize_and_transcribe(monkeypatch):
    monkeypatch.setenv("VOICE_BACKEND", "stub")
    audio = voice.synthesize("hello world")
    assert isinstance(audio, bytes) and len(audio) > 0
    assert voice.synthesize("hello world") == audio          # deterministic
    text = voice.transcribe(audio)
    assert isinstance(text, str) and text


def test_voice_unknown_backend_falls_back(monkeypatch):
    monkeypatch.setenv("VOICE_BACKEND", "nope")
    assert isinstance(voice.synthesize("x"), bytes)
    assert isinstance(voice.transcribe(b"abc"), str)


from core import email_sender


def test_email_stub_returns_true_and_never_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    assert email_sender.send_email("a@b.com", "Hi", "Body") is True


def test_email_smtp_without_config_failopen(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    # No SMTP config: returns False, never raises.
    assert email_sender.send_email("a@b.com", "Hi", "Body") is False


from datetime import timedelta

from core import marketing
from core import workspace_factory as wf
from core.llm_providers import CompletionResponse


def _echo_complete(req, provider):
    return CompletionResponse(text=f"BODY[{req.system[:12]}]", provider=provider,
                              model=req.model, stubbed=True)


def _ws_with_marketing(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["marketing"],
        )
        member = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        contact = MarketingContact(id="ct1", workspace_id=ws.id, name="Lee",
                                   email="lee@globex.com", company="Globex")
        s.add(contact)
        s.commit()
        return ws.id, member.id, "ct1"


def test_send_outreach_persists_and_schedules(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        member = s.get(WorkspaceMember, member_id)
        contact = s.get(MarketingContact, ct_id)
        msg = marketing.send_outreach(s, ws_id, member, contact, "demo our product",
                                      followup_days=3)
        assert msg.send_status == "sent"
        assert msg.kind == "outreach"
        assert msg.next_followup_at is not None
        assert msg.body.startswith("BODY[")
        assert s.get(MarketingContact, ct_id).status == "contacted"


def test_followup_cadence_bounded(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    monkeypatch.setenv("MARKETING_MAX_FOLLOWUPS", "2")
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        member = s.get(WorkspaceMember, member_id)
        contact = s.get(MarketingContact, ct_id)
        outreach = marketing.send_outreach(s, ws_id, member, contact, "demo", followup_days=3)
        outreach.next_followup_at = utcnow() - timedelta(days=1)
        s.add(outreach); s.commit()

        due = marketing.due_followups(s, ws_id)
        assert len(due) == 1

        f1 = marketing.send_followup(s, member, due[0], followup_days=0)
        assert f1.kind == "followup" and f1.send_status == "sent"
        s.refresh(outreach)
        assert outreach.followup_count == 1

        due2 = marketing.due_followups(s, ws_id)
        marketing.send_followup(s, member, due2[0], followup_days=0)
        s.refresh(outreach)
        assert outreach.followup_count == 2
        assert marketing.due_followups(s, ws_id) == []   # capped out


def test_find_marketing_member(db):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        m = marketing.find_marketing_member(s, ws_id)
        assert m is not None and m.role == "marketing"


from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    import dashboard.main as dash
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    return TestClient(dash.app)


def test_marketing_page_and_contact_and_outreach(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    r = client.get(f"/workspaces/{ws_id}/marketing")
    assert r.status_code == 200

    r = client.post(f"/workspaces/{ws_id}/marketing/contacts", data={
        "name": "Dana", "email": "dana@x.com", "company": "X"}, follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/workspaces/{ws_id}/marketing/outreach", data={
        "contact_id": ct_id, "goal": "demo our product"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        msgs = s.exec(select(OutreachMessage).where(
            OutreachMessage.workspace_id == ws_id)).all()
        assert len(msgs) == 1 and msgs[0].send_status == "sent"


def test_run_followups_route(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    client.post(f"/workspaces/{ws_id}/marketing/outreach", data={
        "contact_id": ct_id, "goal": "demo"}, follow_redirects=False)
    with Session(db) as s:
        om = s.exec(select(OutreachMessage).where(
            OutreachMessage.workspace_id == ws_id)).one()
        om.next_followup_at = utcnow() - timedelta(days=1)
        s.add(om); s.commit()
    r = client.post(f"/workspaces/{ws_id}/marketing/followups/run", follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        followups = s.exec(select(OutreachMessage).where(
            OutreachMessage.kind == "followup")).all()
        assert len(followups) == 1


def test_voice_preview_returns_audio(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    r = client.post(f"/workspaces/{ws_id}/marketing/voice/preview",
                    data={"text": "Hello there"})
    assert r.status_code == 200
    assert len(r.content) > 0
