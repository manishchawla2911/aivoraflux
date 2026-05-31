# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Python orchestration system that runs a fleet of 11 specialized AI agents to take a client brief through to a tested, deployed codebase. The design is fully specified in five root-level Markdown docs that the implementation tracks closely:

- `MASTER_PLAN.md` — architecture, tech stack, build order, env vars
- `AGENT_SPECS.md` — per-agent input/output contracts and behaviour
- `ORCHESTRATOR_SPEC.md` — orchestrator state machine and DB schema
- `SCHEMAS.md` — Pydantic v2 model definitions
- `AGENT_PROMPTS.md` — source-of-truth system prompts (also copied to `prompts/*.md`)

**When changing agent behaviour, schemas, or orchestrator transitions, read the matching spec doc first** — code and spec are expected to stay in sync.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env          # fill ANTHROPIC_API_KEY etc.

python main.py                # FastAPI on :8000 + orchestrator in background
pytest tests/ -v              # full suite — passes without ANTHROPIC_API_KEY (LLMs are mocked)
pytest tests/test_orchestrator.py::test_name -v   # single test
```

`pytest.ini` sets `asyncio_mode = auto`, so async tests don't need an `@pytest.mark.asyncio` decorator.

## Architecture

Three structural layers, plus the runtime engine:

1. **`schemas/`** — Pydantic v2 models that are the agent I/O contract. Every agent validates its input and output against these. `extra="forbid"` is mandated by spec; preserve it.
2. **`agents/`** — One file per agent, all subclassing `agents/base_agent.py::BaseAgent`. `run(input: dict) -> AgentResult` is the only abstract method; `run_with_retry` handles the 3-attempt exponential backoff. Three agent shapes:
   - **Thinking agents** (`clarification`, `architect`, `task_planner`, `review`, `docs_delivery`) — call the Anthropic API via `agents/_anthropic_client.py`, validate JSON output against a schema, re-prompt once on validation error.
   - **Builder agents** (`backend`, `frontend`, `integration`, `devops`) — all extend `agents/_builder_base.py::BuilderAgentBase`, which renders a per-task `CLAUDE.md`, spawns a Claude Code subprocess, parses its JSON summary, and commits to a branch (`agent/{type}/{task_id}`). Subclasses only override `agent_type`, `branch_pattern`, and `prompt_filename`.
   - **Validation agents** (`test_writer`, `security_quality`) — Anthropic API + tool use; run real shell commands (pytest, semgrep, bandit) against the builder's output.
3. **`core/`** — Runtime infrastructure:
   - `orchestrator.py` — drives the project state machine (`PROJECT_STATES` + `VALID_TRANSITIONS`), polls the task graph for ready tasks, dispatches agents under a semaphore (`MAX_PARALLEL_AGENTS`, default 4). Does NOT make product decisions — flags to humans via `notifier`.
   - `task_graph.py` — wraps the `TaskGraph` schema; `get_ready_tasks()` returns tasks whose dependencies are all done.
   - `event_bus.py` — asyncio pub/sub. Events: `agent.completed`, `agent.failed`, `agent.flag_human`, `validation.complete`, `project.phase_done`, `observability.anomaly`.
   - `state.py` — SQLModel tables (`Project`, `Task`, `AgentMessage`, `HumanDecision`) backed by SQLite (`agent_fleet.db` by default; override via `DATABASE_URL`).
   - `git_manager.py`, `notifier.py` — GitHub branches/PRs and Slack webhook integrations.
   - `observability.py` — the AI observability layer. `record_event(...)` is a **fail-open** telemetry sink (a broken write never propagates into an agent's run path) writing the `AgentEvent` table; it stores sizes/metadata, **never raw input/output text**. `compute_metrics()` aggregates per-agent + fleet metrics; `detect_anomalies()` runs deterministic rule + rolling-baseline detectors (error rate, latency p95, cost/retry storms, guardrail blocks, stalled tasks, telemetry gaps) with `min_samples` guards; `sweep_and_notify()`/`run_sweep_loop()` publish `observability.anomaly` (opt-in via `OBSERVABILITY_SWEEP_SECONDS`). The Orchestrator ("manager") subscribes and escalates critical anomalies via `notifier`. Instrumentation lives in `BaseAgent.run_with_retry`, the orchestrator dispatch, and `/agents/{id}/run`. **When passing an `Anomaly` (or any dict with a `message` key) to a logger, never use `extra=` — `message` collides with a reserved `LogRecord` attribute.**
   - `state.py` also hosts the **Agent Studio** product tables (`Agent`, `AgentRun`, `ProviderConfig`, `GuardrailProfile`), the observability `AgentEvent` table, and exposes `utcnow()` — use it instead of the deprecated `datetime.utcnow()`.
4. **`dashboard/`** — FastAPI app served by `main.py`. DB init and the orchestrator boot both run from the app's **lifespan** handler (not the deprecated `@app.on_event`); `main.py` attaches the orchestrator via `dashboard.main.on_startup`. Both layers share the one process. Fleet human touch points (PRD approval, architecture approval, flagged decisions, delivery sign-off) funnel through `/projects/{id}/decisions/{did}`.
5. **Agent Studio layer** — a second product on the same app/DB:
   - `core/agent_factory.py` — turns a form or a natural-language brief into an `AgentSpec` (auto-craft via `core/llm_providers.py`, with a deterministic heuristic fallback when no LLM is reachable).
   - `core/llm_providers.py` — pluggable provider registry (`anthropic`, `openai`, `google`, `mistral`, `groq`, `ollama`, `lmstudio`). Each provider degrades to an offline stub when its key/SDK is missing; real-call failures are logged, not silently swallowed.
   - `core/guardrails.py` — composite input/output safety stack (prompt-injection, jailbreak, toxicity, blocked-topics, PII redaction, rate limiting) driven by a `GuardrailProfile`.
   - Routes: `/`, `/pricing`, `/dashboard`, `/studio`, `/agents/*`, `/settings/providers`. The `/agents/{id}/run` endpoint caps input at `MAX_RUN_INPUT_CHARS`.
   - **Observability routes**: `/observability` (HTML), `/observability/api/metrics`, `/observability/api/anomalies` (JSON). Gated by `_admin_gate` — when `ADMIN_TOKEN` is set it requires an `X-Admin-Token` header or `?token=`; when unset the view stays open (demo) but renders an "ungated" warning.

### Key invariants

- **Project state transitions must go through `Orchestrator.transition()`** so they validate against `VALID_TRANSITIONS`. Don't mutate `Project.status` directly.
- **Agent registration in `main.py::build_orchestrator()`** keys agents by phase name for thinking agents and by `AgentType.value` for builder/validation agents — the dispatcher in `Orchestrator._dispatch_task` looks them up by `task.agent_type.value`. New agent types need both an `AgentType` enum entry and a registration line.
- **Builder agents do not generate code themselves** — they prepare a `CLAUDE.md` context file and shell out to a Claude Code subprocess. Tests inject a `claude_code_runner` callable to avoid the subprocess.
- **All agents return `AgentResult`** with status `success | needs_human | failed | blocked`. `needs_human` and `success` short-circuit retries; `failed` retries up to `max_retries`.
- **Prompts live in `prompts/*.md`** and are loaded at agent init. They are copies of the canonical text in `AGENT_PROMPTS.md` — keep both in sync if editing.

## Testing notes

- Tests don't need real API keys. Thinking agents accept an injected Anthropic client; builder agents accept an injected `claude_code_runner`; the dashboard is exercised via FastAPI's `TestClient`.
- `conftest.py` adds the repo root to `sys.path` so `from core...` / `from agents...` imports work.
- `tests/` is organised one file per phase (schemas, state, task graph, orchestrator, thinking agents, builder agents, validation agents, end-to-end, dashboard).
