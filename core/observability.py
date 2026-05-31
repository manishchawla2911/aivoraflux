"""AI observability layer — telemetry capture, metrics, and anomaly detection.

This module is the single source of truth for watching agent behaviour across
BOTH products on this app: the Agent Fleet pipeline and Agent Studio runs.

Design principles (see docs/superpowers/specs/2026-05-26-ai-observability-design.md):

- **Fail-open.** ``record_event`` must NEVER raise into an agent's run path. A
  broken telemetry write is logged and swallowed — observing the system can't be
  allowed to break it.
- **Privacy.** We store sizes/counts/metadata, never raw input/output text, so
  telemetry can't re-introduce the PII/secret leakage guardrails exist to stop.
- **Cheap & bounded.** Inserts are lightweight; aggregation scans a bounded
  number of recent rows rather than the whole table.
- **Deterministic anomalies.** Threshold rules + rolling baselines with
  ``min_samples`` guards. No LLM judge — the watchdog must not itself be a
  fallible, paid agent that can hallucinate or fail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from core.state import AgentEvent, Task as TaskRow, get_engine, utcnow

logger = logging.getLogger(__name__)

# Selectable dashboard windows.
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 604800}

# Upper bound on rows pulled into memory for a single metrics computation, so a
# huge event table can never blow up the dashboard (cf. PERF-1 in IMPROVEMENTS).
MAX_SCAN = int(os.getenv("OBS_MAX_SCAN", "20000"))

# Event types that represent a completed invocation (vs. an intermediate retry).
_TERMINAL_TYPES = {"completed", "failed", "escalated", "run", "guardrail_block"}


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# ───────────────────────────── telemetry sink ─────────────────────────────

def record_event(
    *,
    event_type: str,
    source: str = "fleet",
    status: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    attempt: int = 0,
    retry_count: int = 0,
    duration_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    guardrail_verdict: Optional[str] = None,
    error: Optional[str] = None,
    input_chars: int = 0,
    output_chars: int = 0,
    meta: Optional[dict] = None,
) -> None:
    """Persist a single telemetry event. Fail-open: never raises.

    Any failure (DB locked, bad value, disposed engine) is logged and
    swallowed — telemetry must not interfere with the agent it observes.
    """
    try:
        ev = AgentEvent(
            id=str(uuid.uuid4()),
            ts=utcnow(),
            source=source,
            event_type=event_type,
            status=status,
            agent_name=agent_name,
            agent_type=agent_type,
            agent_id=agent_id,
            project_id=project_id,
            task_id=task_id,
            attempt=int(attempt or 0),
            retry_count=int(retry_count or 0),
            duration_ms=int(duration_ms or 0),
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cost_usd=float(cost_usd or 0.0),
            guardrail_verdict=guardrail_verdict,
            error=(str(error)[:500] if error else None),
            input_chars=int(input_chars or 0),
            output_chars=int(output_chars or 0),
            meta_json=(json.dumps(meta)[:2000] if meta else None),
        )
        with Session(get_engine()) as session:
            session.add(ev)
            session.commit()
    except Exception:  # noqa: BLE001 — telemetry is best-effort, never fatal
        logger.exception("observability.record_event_failed")


# ───────────────────────────── metrics ─────────────────────────────

def _pct(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_values:
        return 0
    rank = max(1, math.ceil((p / 100.0) * len(sorted_values)))
    return int(sorted_values[min(rank, len(sorted_values)) - 1])


def _aggregate(name: str, events: list[AgentEvent]) -> dict[str, Any]:
    runs = len(events)
    errors = sum(1 for e in events if e.status == "failed")
    escalations = sum(
        1 for e in events
        if e.event_type == "escalated" or e.status in ("needs_human", "blocked")
    )
    blocks = sum(
        1 for e in events
        if e.event_type == "guardrail_block" or e.guardrail_verdict == "blocked"
    )
    durations = sorted(e.duration_ms for e in events if e.duration_ms)
    cost = sum(e.cost_usd for e in events)
    tokens = sum((e.input_tokens + e.output_tokens) for e in events)
    retries = sum(e.retry_count for e in events)
    last_seen = max((e.ts for e in events), default=None)
    return {
        "agent": name,
        "runs": runs,
        "errors": errors,
        "error_rate": round(errors / runs, 4) if runs else 0.0,
        "escalations": escalations,
        "guardrail_blocks": blocks,
        "guardrail_block_rate": round(blocks / runs, 4) if runs else 0.0,
        "retries": retries,
        "retries_per_run": round(retries / runs, 4) if runs else 0.0,
        "p50_ms": _pct(durations, 50),
        "p95_ms": _pct(durations, 95),
        "avg_ms": int(sum(durations) / len(durations)) if durations else 0,
        "cost_usd": round(cost, 6),
        "tokens": tokens,
        "last_seen": last_seen.isoformat() if last_seen else None,
    }


def _terminal_events(session: Session, since, limit: int = MAX_SCAN) -> list[AgentEvent]:
    stmt = (
        select(AgentEvent)
        .where(AgentEvent.ts >= since, AgentEvent.event_type != "attempt")
        .order_by(AgentEvent.ts.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def compute_metrics(window_seconds: int = 86400) -> dict[str, Any]:
    """Aggregate per-agent and fleet-wide metrics over a trailing window."""
    now = utcnow()
    since = now - timedelta(seconds=window_seconds)
    with Session(get_engine()) as session:
        rows = _terminal_events(session, since)

    by_agent: dict[str, list[AgentEvent]] = defaultdict(list)
    for r in rows:
        by_agent[r.agent_name or r.agent_type or "unknown"].append(r)

    agents = [_aggregate(name, evs) for name, evs in by_agent.items()]
    agents.sort(key=lambda a: a["runs"], reverse=True)

    return {
        "window_seconds": window_seconds,
        "generated_at": now.isoformat(),
        "scanned": len(rows),
        "truncated": len(rows) >= MAX_SCAN,
        "fleet": _aggregate("__fleet__", rows),
        "agents": agents,
    }


# ───────────────────────────── anomalies ─────────────────────────────

@dataclass
class Thresholds:
    """Anomaly thresholds. Read from env at construction so tests can override."""
    min_samples: int = field(default_factory=lambda: _envi("OBS_MIN_SAMPLES", 5))
    error_rate: float = field(default_factory=lambda: _envf("OBS_ERROR_RATE", 0.25))
    retries_per_run: float = field(default_factory=lambda: _envf("OBS_RETRIES_PER_RUN", 1.0))
    guardrail_block_rate: float = field(default_factory=lambda: _envf("OBS_BLOCK_RATE", 0.30))
    latency_factor: float = field(default_factory=lambda: _envf("OBS_LATENCY_FACTOR", 3.0))
    cost_factor: float = field(default_factory=lambda: _envf("OBS_COST_FACTOR", 3.0))
    stalled_task_seconds: int = field(default_factory=lambda: _envi("OBS_STALLED_SECONDS", 1800))
    baseline_seconds: int = field(default_factory=lambda: _envi("OBS_BASELINE_SECONDS", 604800))
    cooldown_seconds: int = field(default_factory=lambda: _envi("OBS_COOLDOWN_SECONDS", 300))


@dataclass
class Anomaly:
    key: str            # stable dedup key, e.g. "error_rate:backend"
    detector: str
    severity: str       # info | warning | critical
    agent: str
    metric: str
    value: float
    threshold: float
    message: str
    ts: str = ""


def _detect_stalled_tasks(th: Thresholds, now_iso: str) -> list[Anomaly]:
    """Flag tasks stuck in `running` past the timeout — the log-only blind spot."""
    out: list[Anomaly] = []
    cutoff = utcnow() - timedelta(seconds=th.stalled_task_seconds)
    try:
        with Session(get_engine()) as session:
            stmt = select(TaskRow).where(
                TaskRow.status == "running",
                TaskRow.started_at.is_not(None),
                TaskRow.started_at < cutoff,
            )
            rows = list(session.scalars(stmt).all())
    except Exception:  # noqa: BLE001 — detection is best-effort
        logger.exception("observability.stalled_query_failed")
        return out

    for t in rows:
        age = (utcnow() - t.started_at).total_seconds()
        out.append(Anomaly(
            key=f"stalled_task:{t.id}",
            detector="stalled_task",
            severity="critical",
            agent=t.agent_type or "unknown",
            metric="running_seconds",
            value=round(age, 1),
            threshold=float(th.stalled_task_seconds),
            message=(
                f"Task {t.id} ({t.agent_type or 'unknown'}) has been running for "
                f"{int(age)}s, past the {th.stalled_task_seconds}s stall timeout."
            ),
            ts=now_iso,
        ))
    return out


def detect_anomalies(
    window_seconds: int = 3600,
    baseline_seconds: Optional[int] = None,
) -> list[Anomaly]:
    """Run all detectors and return every current anomaly (no cooldown applied).

    Recent window vs. a longer baseline window for spike detectors. Every
    detector is gated by ``min_samples`` to avoid cold-start false positives.
    """
    th = Thresholds()
    baseline_seconds = baseline_seconds or th.baseline_seconds
    now_iso = utcnow().isoformat()

    recent = compute_metrics(window_seconds)
    baseline = compute_metrics(baseline_seconds)
    base_by_agent = {a["agent"]: a for a in baseline["agents"]}

    out: list[Anomaly] = []
    for a in recent["agents"]:
        name = a["agent"]
        runs = a["runs"]
        if runs < th.min_samples:
            continue

        if a["error_rate"] > th.error_rate:
            sev = "critical" if a["error_rate"] >= min(1.0, th.error_rate * 2) else "warning"
            out.append(Anomaly(
                key=f"error_rate:{name}", detector="error_rate", severity=sev,
                agent=name, metric="error_rate", value=a["error_rate"], threshold=th.error_rate,
                message=(f"{name}: error rate {a['error_rate']:.0%} over {runs} runs "
                         f"exceeds {th.error_rate:.0%}."),
                ts=now_iso,
            ))

        if a["retries_per_run"] > th.retries_per_run:
            out.append(Anomaly(
                key=f"retry_storm:{name}", detector="retry_storm", severity="warning",
                agent=name, metric="retries_per_run", value=a["retries_per_run"],
                threshold=th.retries_per_run,
                message=(f"{name}: {a['retries_per_run']:.1f} retries/run over {runs} runs "
                         f"exceeds {th.retries_per_run}."),
                ts=now_iso,
            ))

        if a["guardrail_block_rate"] > th.guardrail_block_rate:
            out.append(Anomaly(
                key=f"guardrail_blocks:{name}", detector="guardrail_blocks", severity="warning",
                agent=name, metric="guardrail_block_rate", value=a["guardrail_block_rate"],
                threshold=th.guardrail_block_rate,
                message=(f"{name}: guardrails blocked {a['guardrail_block_rate']:.0%} of "
                         f"{runs} runs, above {th.guardrail_block_rate:.0%}."),
                ts=now_iso,
            ))

        b = base_by_agent.get(name)
        if b and b["runs"] >= th.min_samples:
            if b["p95_ms"] > 0 and a["p95_ms"] > b["p95_ms"] * th.latency_factor:
                out.append(Anomaly(
                    key=f"latency_spike:{name}", detector="latency_spike", severity="warning",
                    agent=name, metric="p95_ms", value=float(a["p95_ms"]),
                    threshold=round(b["p95_ms"] * th.latency_factor, 1),
                    message=(f"{name}: p95 latency {a['p95_ms']}ms is >{th.latency_factor}× the "
                             f"baseline {b['p95_ms']}ms."),
                    ts=now_iso,
                ))
            recent_per = a["cost_usd"] / runs if runs else 0.0
            base_per = b["cost_usd"] / b["runs"] if b["runs"] else 0.0
            if base_per > 0 and recent_per > base_per * th.cost_factor:
                out.append(Anomaly(
                    key=f"cost_spike:{name}", detector="cost_spike", severity="warning",
                    agent=name, metric="cost_per_run", value=round(recent_per, 6),
                    threshold=round(base_per * th.cost_factor, 6),
                    message=(f"{name}: cost/run ${recent_per:.4f} is >{th.cost_factor}× the "
                             f"baseline ${base_per:.4f}."),
                    ts=now_iso,
                ))

    # Telemetry gap: active in the baseline window but silent now — guards
    # against an agent that crashed before recording looking "healthy".
    recent_names = {a["agent"] for a in recent["agents"] if a["runs"] > 0}
    for b in baseline["agents"]:
        if b["runs"] >= th.min_samples and b["agent"] not in recent_names:
            out.append(Anomaly(
                key=f"telemetry_gap:{b['agent']}", detector="telemetry_gap", severity="warning",
                agent=b["agent"], metric="runs", value=0.0, threshold=float(th.min_samples),
                message=(f"{b['agent']}: active in the baseline window ({b['runs']} runs) but "
                         f"silent in the last {window_seconds}s — possible crash or stall."),
                ts=now_iso,
            ))

    out.extend(_detect_stalled_tasks(th, now_iso))
    return out


# ───────────────────────────── alerting ─────────────────────────────

# Per-key last-emit times for cooldown/dedup (module-level, process-local).
_last_emit: dict[str, float] = {}


def _should_emit(key: str, cooldown_seconds: int) -> bool:
    now = time.monotonic()
    last = _last_emit.get(key)
    if last is not None and (now - last) < cooldown_seconds:
        return False
    _last_emit[key] = now
    return True


async def sweep_and_notify(event_bus=None, window_seconds: int = 3600) -> list[Anomaly]:
    """Detect anomalies and publish the ones past their cooldown.

    Fail-open. Publishes ``observability.anomaly`` on the event bus so the
    orchestrator ("manager") can react and flag humans. Cooldown/dedup keeps
    this from becoming an alert storm.
    """
    try:
        th = Thresholds()
        anomalies = detect_anomalies(window_seconds)
        emitted: list[Anomaly] = []
        for a in anomalies:
            if _should_emit(a.key, th.cooldown_seconds):
                logger.warning(
                    "observability.anomaly",
                    extra={"anomaly_key": a.key, "severity": a.severity,
                           "detector": a.detector, "agent": a.agent},
                )
                if event_bus is not None:
                    await event_bus.publish("observability.anomaly", asdict(a))
                emitted.append(a)
        return emitted
    except Exception:  # noqa: BLE001 — the sweep must never crash its host loop
        logger.exception("observability.sweep_failed")
        return []


async def run_sweep_loop(event_bus, interval_seconds: int, window_seconds: int = 3600) -> None:
    """Opt-in background sweep. Started from the app lifespan when enabled."""
    logger.info("observability.sweep_loop_started", extra={"interval": interval_seconds})
    while True:
        await sweep_and_notify(event_bus=event_bus, window_seconds=window_seconds)
        await asyncio.sleep(interval_seconds)
