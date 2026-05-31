"""Create/read/update tests for every SQLite model."""
from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, select

from core.state import (
    AgentMessage,
    HumanDecision,
    Project,
    Task,
    ValidationReport,
    get_engine,
    init_db,
    utcnow,
)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Reset the singleton engine for this test
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    with Session(get_engine()) as s:
        yield s


def test_project_crud(session: Session):
    p = Project(id=str(uuid.uuid4()), name="Acme", client_name="ACME Corp", status="CREATED")
    session.add(p)
    session.commit()

    fetched = session.get(Project, p.id)
    assert fetched is not None
    assert fetched.name == "Acme"
    assert fetched.status == "CREATED"

    fetched.status = "BUILDING"
    fetched.updated_at = utcnow()
    session.add(fetched)
    session.commit()

    again = session.get(Project, p.id)
    assert again.status == "BUILDING"


def test_task_crud(session: Session):
    p = Project(id="proj-1", name="P", status="CREATED")
    session.add(p)
    session.commit()

    t = Task(
        id="BE-001",
        project_id="proj-1",
        title="Build users API",
        agent_type="backend",
        status="pending",
        depends_on='[]',
        context_package='{"k":"v"}',
    )
    session.add(t)
    session.commit()

    fetched = session.get(Task, "BE-001")
    assert fetched.title == "Build users API"
    assert fetched.retry_count == 0

    fetched.status = "running"
    fetched.retry_count = 1
    session.add(fetched)
    session.commit()
    assert session.get(Task, "BE-001").retry_count == 1


def test_agent_message_crud(session: Session):
    msg_id = str(uuid.uuid4())
    msg = AgentMessage(
        id=msg_id, project_id="proj-1", task_id="BE-001",
        from_agent="architect", to_agent="task_planner",
        type="output", payload='{"hello":"world"}',
    )
    session.add(msg)
    session.commit()

    fetched = session.get(AgentMessage, msg_id)
    assert fetched.from_agent == "architect"

    rows = session.exec(select(AgentMessage).where(AgentMessage.project_id == "proj-1")).all()
    assert len(rows) == 1


def test_human_decision_crud(session: Session):
    d = HumanDecision(
        id="dec-1", project_id="proj-1", decision_type="prd_approval",
        context='{"summary":"hi"}', options='["approve","reject"]',
    )
    session.add(d)
    session.commit()

    fetched = session.get(HumanDecision, "dec-1")
    assert fetched.reminder_count == 0

    fetched.chosen_option = "approve"
    fetched.decided_at = utcnow()
    fetched.reminder_count = 2
    session.add(fetched)
    session.commit()

    again = session.get(HumanDecision, "dec-1")
    assert again.chosen_option == "approve"
    assert again.reminder_count == 2


def test_validation_report_crud(session: Session):
    r = ValidationReport(
        id="vr-1", project_id="proj-1", task_id="BE-001",
        report_type="test", report='{"passed":1}', status="passed",
    )
    session.add(r)
    session.commit()

    fetched = session.get(ValidationReport, "vr-1")
    assert fetched.report_type == "test"
    assert fetched.status == "passed"
