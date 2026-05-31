# Autonomous Agent Fleet + Agent Studio

This repo hosts **two layers that share one FastAPI process and SQLite store**:

1. **Agent Fleet** — a pipeline of 11 specialized AI agents that turn a client
   brief into a tested, documented, deployed codebase, with the human involved at
   four touch points. Design docs: `MASTER_PLAN.md`, `AGENT_SPECS.md`,
   `ORCHESTRATOR_SPEC.md`, `SCHEMAS.md`, `AGENT_PROMPTS.md`.
2. **Agent Studio** — a product surface for crafting and running your own
   single-purpose AI agents (manual form or natural-language auto-craft), against
   any of several LLM providers, behind a configurable guardrail stack.

## What's here

```
agents/    # 11 fleet-agent implementations + builder base + Anthropic client
core/      # Orchestrator, event bus, task graph, state (SQLite), notifier, git manager
           #   Agent Studio: agent_factory.py, guardrails.py, llm_providers.py
           #   Observability: observability.py (telemetry, metrics, anomaly detection)
schemas/   # Pydantic v2 schemas for every fleet-agent I/O
prompts/   # System prompts for every fleet agent
templates/ # proof_report.html, review_summary.md
dashboard/ # FastAPI app + Jinja templates (fleet touch points + Agent Studio UI)
tests/     # Phase-by-phase test suite — 117 tests, all green
main.py    # Entry point — wires orchestrator + dashboard together
```

### Agent Studio routes

| Route | Purpose |
|---|---|
| `/` , `/pricing` | Marketing pages |
| `/dashboard` | Crafted-agent list + projects + today's run count |
| `/studio` | Manual + auto-craft agent builder |
| `POST /agents/manual` , `POST /agents/auto` | Create an agent |
| `GET /agents/{id}` , `POST /agents/{id}/run` , `POST /agents/{id}/delete` | Agent detail / run / delete |
| `/settings/providers` | LLM provider status |
| `/observability` (+ `/observability/api/metrics`, `/observability/api/anomalies`) | AI observability dashboard — per-agent metrics + flagged anomalies |

Crafted agents (`core/agent_factory.py`) run through a pluggable provider
registry (`core/llm_providers.py`: Anthropic, OpenAI, Google, Mistral, Groq,
Ollama, LM Studio — each degrading to an offline stub when no key is present)
and a layered safety stack (`core/guardrails.py`: prompt-injection / jailbreak /
toxicity / blocked-topic / PII redaction / rate limiting). The crafted-agent,
run-log, provider-config, and guardrail-profile tables live alongside the fleet
tables in `core/state.py`.

## AI observability

Every agent invocation — both fleet pipeline agents and Agent Studio runs — is
recorded to a unified `AgentEvent` telemetry stream (`core/observability.py`).
The capture path is **fail-open** (a broken telemetry write can never break the
agent it observes) and **privacy-preserving** (it stores sizes/counts/metadata,
never raw input/output text).

`/observability` gives the manager (the orchestrator) and a platform admin a
live view: per-agent error rate, latency p50/p95, cost, tokens, retries,
guardrail-block rate, and last-seen, plus a **flagged-anomalies** panel.
Anomalies are detected with deterministic rules + rolling baselines (no LLM
judge), each guarded by a minimum-sample count to avoid cold-start noise:

- error-rate / retry-storm / guardrail-block spikes
- latency-p95 and cost-per-run spikes vs. a rolling baseline
- **stalled tasks** (stuck `running` past a timeout — invisible to log-only tooling)
- **telemetry gaps** (an agent active in the baseline window but suddenly silent)

Critical anomalies are published on the `observability.anomaly` event; the
orchestrator subscribes and escalates them to Slack. An opt-in background sweep
(`OBSERVABILITY_SWEEP_SECONDS`) flags proactively; otherwise metrics and
anomalies are computed on every dashboard load. The dashboard is gated by an
optional `ADMIN_TOKEN` (`X-Admin-Token` header or `?token=`); unset leaves it
open for the demo with a visible warning.

> Production note: this is an in-process implementation suited to the SQLite
> demo. A real deployment should export to OpenTelemetry → Prometheus/Grafana and
> add retention/rollup of the event table.

## Setup

```
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, GITHUB_TOKEN, SLACK_WEBHOOK_URL (optional)
```

## Run

```
python main.py
```

Then open <http://localhost:8000> for the Agent Studio landing page, or
<http://localhost:8000/projects> for the delivery-pipeline view. The orchestrator
runs in the background and surfaces decisions to you via the dashboard and Slack.

The server binds to `127.0.0.1` by default; set `HOST=0.0.0.0` to expose it on
all interfaces (note the dashboard is auth-free, so only do this on a trusted
network).

## Tests

```
pytest tests/ -v
```

All Phase 1–6 tests pass without an Anthropic API key (the LLM-using agents
are exercised via injected mock clients).

## Human touch points

| Touch point | Where |
|---|---|
| PRD approval | `/projects/{id}/decisions/{did}` after clarification |
| Architecture approval | same route after architecture is generated |
| Flagged decision | same route, triggered by Review Agent flags |
| Delivery sign-off | same route, after docs/delivery agent finishes |

The Slack notifier (when `SLACK_WEBHOOK_URL` is set) posts a link straight
to the decision page.

## Environment variables

See `.env.example`.
