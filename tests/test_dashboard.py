"""Smoke tests for the FastAPI dashboard."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from core.state import HumanDecision, Project, get_engine, init_db
from dashboard.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'dash.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return TestClient(app)


def test_index_empty(client):
    # `/` is now the Agent Studio marketing page; project list moved to `/projects`.
    landing = client.get("/")
    assert landing.status_code == 200
    assert "Agent Studio" in landing.text

    r = client.get("/projects")
    assert r.status_code == 200
    assert "Delivery projects" in r.text


def test_create_project_then_view_it(client):
    r = client.post(
        "/projects/new",
        data={"name": "My Project", "client_name": "Acme"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    detail = client.get(location)
    assert detail.status_code == 200
    assert "My Project" in detail.text
    assert "Acme" in detail.text


def test_decision_flow(client):
    pid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    with Session(get_engine()) as s:
        s.add(Project(id=pid, name="P", client_name="C", status="AWAITING_PRD_APPROVAL"))
        s.add(HumanDecision(
            id=did, project_id=pid, decision_type="prd_approval",
            context=json.dumps({"summary": "Looks good"}),
            options=json.dumps(["approve", "reject"]),
        ))
        s.commit()

    # GET decision page
    r = client.get(f"/projects/{pid}/decisions/{did}")
    assert r.status_code == 200
    assert "approve" in r.text
    assert "reject" in r.text

    # POST a choice
    r = client.post(
        f"/projects/{pid}/decisions/{did}",
        data={"chosen_option": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Verify persisted
    with Session(get_engine()) as s:
        d = s.get(HumanDecision, did)
        assert d.chosen_option == "approve"
        assert d.decided_at is not None


def test_decision_not_found_returns_404(client):
    r = client.get("/projects/nope/decisions/nope")
    assert r.status_code == 404
