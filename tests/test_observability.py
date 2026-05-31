"""Tests for the AI observability layer: telemetry, metrics, anomalies, dashboard."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import Session, select

import core.observability as obs
from core.observability import (
    Thresholds, compute_metrics, detect_anomalies, record_event, sweep_and_notify,
)
from core.state import AgentEvent, Task as TaskRow, get_engine, init_db, utcnow
from dashboard.main import app


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'obs.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    # Clear cooldown state so alert dedup doesn't leak across tests.
    obs._last_emit.clear()
    return db_url


@pytest.fixture()
def client(db):
    return TestClient(app)


def _add_event(*, agent_name="backend", status="success", event_type="completed",
               duration_ms=100, cost_usd=0.0, retry_count=0, guardrail_verdict=None,
               source="fleet", ts_offset_seconds=0):
    """Insert a telemetry row with a controllable timestamp."""
    ev = AgentEvent(
        id=str(uuid.uuid4()),
        ts=utcnow() - timedelta(seconds=ts_offset_seconds),
        source=source,
        agent_name=agent_name,
        event_type=event_type,
        status=status,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        retry_count=retry_count,
        guardrail_verdict=guardrail_verdict,
    )
    with Session(get_engine()) as s:
        s.add(ev)
        s.commit()


def _count_events() -> int:
    with Session(get_engine()) as s:
        return s.scalar(select(func.count()).select_from(AgentEvent)) or 0


# ───────────────────────── telemetry sink ─────────────────────────

def test_record_event_persists(db):
    record_event(event_type="completed", status="success", agent_name="backend",
                 duration_ms=42, cost_usd=0.01)
    assert _count_events() == 1


def test_record_event_is_fail_open(db, monkeypatch):
    """A broken DB layer must not propagate out of record_event."""
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(obs, "get_engine", boom)
    # Must not raise.
    record_event(event_type="completed", status="success", agent_name="backend")


def test_record_event_stores_no_raw_text(db):
    """The model only has size fields, never raw input/output columns."""
    cols = set(AgentEvent.model_fields.keys())
    assert "input_chars" in cols and "output_chars" in cols
    assert "input_text" not in cols and "output_text" not in cols


# ───────────────────────── metrics ─────────────────────────

def test_compute_metrics_aggregates(db):
    for _ in range(3):
        _add_event(agent_name="backend", status="success", duration_ms=100)
    _add_event(agent_name="backend", status="failed", event_type="failed", duration_ms=200)

    m = compute_metrics(86400)
    agents = {a["agent"]: a for a in m["agents"]}
    assert agents["backend"]["runs"] == 4
    assert agents["backend"]["errors"] == 1
    assert agents["backend"]["error_rate"] == 0.25
    assert m["fleet"]["runs"] == 4


def test_compute_metrics_percentiles(db):
    for d in (10, 20, 30, 40, 100):
        _add_event(agent_name="frontend", duration_ms=d)
    m = compute_metrics(86400)
    fe = next(a for a in m["agents"] if a["agent"] == "frontend")
    assert fe["p95_ms"] == 100
    assert fe["p50_ms"] in (20, 30)


# ───────────────────────── anomaly detectors ─────────────────────────

def test_error_rate_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "3")
    for _ in range(4):
        _add_event(agent_name="erratic", status="failed", event_type="failed")
    _add_event(agent_name="erratic", status="success")

    anomalies = detect_anomalies(86400)
    err = [a for a in anomalies if a.detector == "error_rate" and a.agent == "erratic"]
    assert err and err[0].severity == "critical"


def test_min_sample_guard_suppresses_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "5")
    for _ in range(2):
        _add_event(agent_name="quiet", status="failed", event_type="failed")
    anomalies = detect_anomalies(86400)
    assert not [a for a in anomalies if a.agent == "quiet"]


def test_retry_storm_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "3")
    for _ in range(4):
        _add_event(agent_name="flaky", status="success", retry_count=2)
    anomalies = detect_anomalies(86400)
    assert [a for a in anomalies if a.detector == "retry_storm" and a.agent == "flaky"]


def test_stalled_task_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_STALLED_SECONDS", "60")
    with Session(get_engine()) as s:
        s.add(TaskRow(
            id="t-stuck", project_id="p1", agent_type="backend",
            status="running", started_at=utcnow() - timedelta(seconds=600),
        ))
        s.commit()
    anomalies = detect_anomalies(86400)
    stalled = [a for a in anomalies if a.detector == "stalled_task"]
    assert stalled and stalled[0].severity == "critical"


def test_telemetry_gap_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "2")
    # Active in the baseline window (~2h ago) but silent in the last hour.
    for _ in range(3):
        _add_event(agent_name="vanished", status="success", ts_offset_seconds=7200)
    anomalies = detect_anomalies(3600)  # recent = 1h, baseline default = 7d
    assert [a for a in anomalies if a.detector == "telemetry_gap" and a.agent == "vanished"]


# ───────────────────────── alerting ─────────────────────────

async def test_sweep_publishes_anomaly(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "3")

    class _FakeBus:
        def __init__(self):
            self.published = []

        async def publish(self, event, data=None):
            self.published.append((event, data))

    for _ in range(4):
        _add_event(agent_name="sweepy", status="failed", event_type="failed")

    bus = _FakeBus()
    emitted = await sweep_and_notify(event_bus=bus, window_seconds=86400)
    assert emitted
    assert any(e == "observability.anomaly" for e, _ in bus.published)


async def test_sweep_cooldown_dedups(db, monkeypatch):
    monkeypatch.setenv("OBS_MIN_SAMPLES", "3")
    monkeypatch.setenv("OBS_COOLDOWN_SECONDS", "300")
    for _ in range(4):
        _add_event(agent_name="noisy", status="failed", event_type="failed")
    first = await sweep_and_notify(window_seconds=86400)
    second = await sweep_and_notify(window_seconds=86400)
    assert first and not second  # cooldown suppresses the repeat


# ───────────────────────── dashboard ─────────────────────────

def test_observability_dashboard_renders(client):
    _add_event(agent_name="backend", status="success")
    r = client.get("/observability")
    assert r.status_code == 200
    assert "AI Observability" in r.text


def test_observability_api_metrics(client):
    _add_event(agent_name="backend", status="success")
    r = client.get("/observability/api/metrics?window=24h")
    assert r.status_code == 200
    assert r.json()["fleet"]["runs"] == 1


def test_admin_gate_blocks_without_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    assert client.get("/observability").status_code == 401
    ok = client.get("/observability", headers={"X-Admin-Token": "secret"})
    assert ok.status_code == 200


def test_studio_run_emits_observability_event(client):
    r = client.post(
        "/agents/manual",
        data={"name": "Helper", "system_prompt": "You help.", "category": "support"},
        follow_redirects=False,
    )
    agent_id = r.headers["location"].rsplit("/", 1)[-1]
    run = client.post(f"/agents/{agent_id}/run", data={"input": "hello"})
    assert run.status_code == 200

    with Session(get_engine()) as s:
        events = list(s.scalars(select(AgentEvent).where(AgentEvent.source == "studio")).all())
    assert events and events[0].output_chars >= 0
