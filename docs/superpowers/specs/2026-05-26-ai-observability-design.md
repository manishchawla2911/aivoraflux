# AI Observability Layer — Design

**Date:** 2026-05-26
**Status:** Approved (via session goal directive)
**Scope decision:** Unified — instrument both the Agent Fleet pipeline agents and the Agent Studio user-run agents.
**Anomaly method:** Deterministic rules + rolling statistical baselines (no LLM judge).

## Problem

The platform runs two agent products on one FastAPI/SQLite process: the **Agent Fleet**
pipeline (orchestrator + 11 build agents) and **Agent Studio** (user-crafted agents run via
`/agents/{id}/run`). Studio runs already persist per-run telemetry in `AgentRun`, but the fleet
agents emit only ephemeral `logger.info` lines — there is no persisted, queryable record of agent
behaviour, and no way for the manager (orchestrator) or a human admin to watch health or flag
anomalies. The SSE log stream at `/projects/{id}/logs` is a documented no-op.

## Goals

- Persist lightweight telemetry for **every** agent invocation (fleet + studio).
- Aggregate it into per-agent and fleet-wide metrics (error rate, latency p50/p95, cost, tokens,
  retries, guardrail-block rate, throughput, last-seen).
- Detect anomalies with deterministic rules + rolling baselines and surface them to (a) a human
  admin dashboard and (b) the orchestrator ("manager") via the event bus.

## Anticipated faults (design constraints)

1. **Observer must not break the observed.** `record_event` is fail-open: any error is logged and
   swallowed; it never raises into an agent's run path.
2. **Hot-path cost / SQLite single-writer contention.** Lightweight indexed inserts; metrics use
   SQL aggregation; latency percentiles read a bounded sample, not the whole table.
3. **PII / secret capture.** Telemetry stores sizes/counts/metadata only — never raw input/output
   text. (Guardrails redact output; the observer must not undo that.)
4. **Stalled-task blind spot.** A task left `running` (started, never completed) is invisible to
   log-only observability. The stalled-task detector reads `Task` rows directly.
5. **Alert fatigue / cold-start.** Every detector has a `min_samples` guard; anomalies are keyed
   for dedup + cooldown before notification.
6. **Silent telemetry gaps = false "healthy".** A "telemetry gap" detector flags an agent that was
   active in the baseline window but has gone silent.
7. **Sensitive data on an auth-free app.** The dashboard is gated by an optional `ADMIN_TOKEN`; when
   unset it stays open (demo) but renders an "ungated" warning rather than silently exposing data.
8. **Meta-risk of an LLM observer.** Rejected an LLM-judge detector — the watchdog would itself be a
   fallible, paid agent that can hallucinate or fail.

## Components

- **`core/state.py::AgentEvent`** — append-only telemetry table. Sizes/metadata only.
- **`core/observability.py`** —
  - `record_event(...)` — fail-open writer.
  - `compute_metrics(window_seconds)` — per-agent + fleet aggregation (bounded scan).
  - `detect_anomalies(window_seconds)` — rule + baseline detectors → `list[Anomaly]`.
  - `sweep_and_notify(event_bus, ...)` — dedup/cooldown, publishes `observability.anomaly`.
  - `run_sweep_loop(...)` — opt-in background sweep (`OBSERVABILITY_SWEEP_SECONDS`, default 0=off).
- **Instrumentation (fail-open, additive):** `BaseAgent.run_with_retry` (fleet terminal + failed
  attempts), `Orchestrator` (no-agent dispatch failures; subscribes to `observability.anomaly`),
  `/agents/{id}/run` (studio runs + guardrail blocks).
- **`core/event_bus.py`** — add `observability.anomaly` to `KNOWN_EVENTS`.
- **Dashboard** — `GET /observability` (HTML), `/observability/api/metrics`,
  `/observability/api/anomalies` (JSON), behind `_require_admin`.

## Out of scope (documented, not built)

External OpenTelemetry / Prometheus / Grafana (note in-process design; OTel is the production
path), retention/rollup jobs (indexes + bounded queries now), LLM-judge anomaly detection.

## Testing

`tests/test_observability.py`: fail-open `record_event`, metrics aggregation, each detector
(positive + negative + min-sample), dashboard route + admin gate (200/401), studio run emits an
event. Passes without an API key; keeps the existing 101 tests green.
