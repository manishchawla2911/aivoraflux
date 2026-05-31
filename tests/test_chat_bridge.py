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


from core import chat_bridge as cb


def test_parse_inbound_telegram():
    msg = cb.parse_inbound("telegram", {"message": {"chat": {"id": 42}, "text": "/ceo hi"}})
    assert msg is not None
    assert msg.external_id == "42" and msg.text == "/ceo hi"


def test_parse_inbound_slack_message_and_challenge():
    msg = cb.parse_inbound("slack", {"event": {"channel": "C9", "text": "hey @CEO"}})
    assert msg.external_id == "C9" and msg.text == "hey @CEO"
    chal = cb.parse_inbound("slack", {"type": "url_verification", "challenge": "abc"})
    assert chal.challenge == "abc"
    # bot's own message ignored
    assert cb.parse_inbound("slack", {"event": {"channel": "C9", "text": "x", "bot_id": "B1"}}) is None


def test_parse_inbound_whatsapp_and_junk():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "555", "text": {"body": "hello"}}]}}]}]}
    msg = cb.parse_inbound("whatsapp", payload)
    assert msg.external_id == "555" and msg.text == "hello"
    assert cb.parse_inbound("telegram", {"nothing": True}) is None


def test_send_message_failopen_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert cb.send_message("telegram", "42", "hi", token=None) is False
