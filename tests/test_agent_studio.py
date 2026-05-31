"""Tests for the Agent Studio factory (core/agent_factory.py) and its dashboard routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from core.agent_factory import _heuristic_spec, _slugify, auto_craft
from core.state import Agent, get_engine, init_db


# ----------------------------- factory unit tests ----------------------------

def test_slugify():
    assert _slugify("My Cool Agent!") == "my-cool-agent"
    assert _slugify("   ") == "agent"


def test_heuristic_spec_picks_category_and_tools():
    spec = _heuristic_spec("A research agent that searches the web for news")
    assert spec["category"] == "research"
    assert "web_search" in spec["suggested_tools"]


def test_auto_craft_falls_back_without_llm(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    spec = auto_craft("A coding agent that writes python scripts", provider="anthropic")
    assert spec.name
    assert spec.crafted_mode == "auto"
    # Only catalog tool ids survive.
    from core.agent_factory import TOOL_CATALOG
    valid = {t["id"] for t in TOOL_CATALOG}
    assert all(t in valid for t in spec.tools)


# ----------------------------- route tests -----------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'studio.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    from dashboard.main import app
    return TestClient(app)


def _create_manual(client, **overrides):
    data = {
        "name": "Helper",
        "description": "A helpful agent",
        "category": "support",
        "system_prompt": "You are helpful.",
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "temperature": "0.7",
        "max_tokens": "2048",
    }
    data.update(overrides)
    r = client.post("/agents/manual", data=data, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rstrip("/").split("/")[-1]


def test_create_manual_agent_then_view(client):
    agent_id = _create_manual(client)
    detail = client.get(f"/agents/{agent_id}")
    assert detail.status_code == 200
    assert "Helper" in detail.text


def test_manual_create_clamps_numeric_fields(client):
    # SEC-3: out-of-range values must be clamped, not passed through.
    agent_id = _create_manual(client, temperature="50", max_tokens="99999999")
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
    assert agent.temperature <= 2.0
    assert agent.max_tokens <= 32000


def test_run_agent_returns_stub_when_no_key(client):
    agent_id = _create_manual(client)
    r = client.post(f"/agents/{agent_id}/run", data={"input": "Say hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["guardrail_verdict"] == "passed"
    assert body["stubbed"] is True


def test_run_agent_rejects_oversized_input(client, monkeypatch):
    # SEC-2: input over the cap is rejected with 413.
    monkeypatch.setattr("dashboard.main.MAX_RUN_INPUT_CHARS", 10)
    agent_id = _create_manual(client)
    r = client.post(f"/agents/{agent_id}/run", data={"input": "x" * 50})
    assert r.status_code == 413
    assert "too large" in r.json()["error"]


def test_delete_agent(client):
    agent_id = _create_manual(client)
    r = client.post(f"/agents/{agent_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/agents/{agent_id}").status_code == 404


def test_auto_craft_route(client):
    r = client.post(
        "/agents/auto",
        data={"brief": "An agent that researches AI news on the web"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    agent_id = r.headers["location"].rstrip("/").split("/")[-1]
    assert client.get(f"/agents/{agent_id}").status_code == 200
