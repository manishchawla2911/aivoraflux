# Improvements & Loophole Checklist

> Audit of the repository performed 2026-05-26. Baseline at audit time: **76 tests passing**,
> Python 3.12.3, no git repository, no `.gitignore`.
>
> This document is the source of truth for the remediation work. Each actionable item has an ID,
> a rationale, the file(s) it touches, and a checkbox. The orchestrator/full-pipeline rewrite and
> an auth system are explicitly **out of scope** (see the last section) — they are large features,
> not loophole fixes, and would risk the green test suite.
>
> **Status (2026-05-26): all 16 checklist items implemented. Suite now at 101 passing
> (76 baseline + 25 new).**

## Method

Read every module in `agents/`, `core/`, `dashboard/`, `schemas/`, the entry point, the specs, and
the test suite. Cross-checked code against the five spec docs and against the actual runtime
behaviour (binding, deprecations, error handling, data flow). Findings below are grouped by class;
the **Checklist** section is the concrete, verifiable work to perform.

---

## Findings

### A. Security & safety

- **SEC-1 — Server binds `0.0.0.0`.** `main.py` runs uvicorn on `host="0.0.0.0"`, exposing the
  dashboard on every network interface. The dashboard is "auth-free for demo" and its
  `/agents/{id}/run` endpoint spends real API money, so an open bind is a money/abuse risk.
- **SEC-2 — No input-size cap on `/agents/{id}/run`.** A caller can post an arbitrarily large
  `input`, driving token cost / latency. Output is capped by guardrails; input is not.
- **SEC-3 — Unvalidated numeric form fields.** `create_agent_manual` casts `temperature` and
  `max_tokens` from form data with no bounds; `temperature=50` or a huge `max_tokens` flows
  straight into the provider call.
- **SEC-4 — Over-broad PII regexes.** The credit-card pattern `(?:\d[ -]*?){13,16}` matches many
  non-card digit runs. Risk of mangling legitimate output when `pii_redaction` is on.

### B. Correctness / bugs

- **BUG-1 — Dead duplicate backoff.** `agents/base_agent.py` computes `backoff` twice; the first
  assignment is immediately overwritten. Confusing dead code.
- **BUG-2 — `"blocked"` status mishandled by orchestrator.** `BaseAgent.run_with_retry` treats
  `blocked` as terminal, but `Orchestrator._dispatch_task` only branches on `success` /
  `needs_human`; `blocked` falls through to the failure path instead of escalating to a human.
- **BUG-3 — Silent provider fallback.** Every provider in `core/llm_providers.py` wraps the real
  call in `except Exception: return _stub_complete(...)` with no logging. Real auth/rate-limit
  errors are silently masked as "offline stub" responses — misleading and undebuggable.
- **BUG-4 — Engine leak on override.** `core/state.get_engine` recreates `_engine` whenever a
  `database_url` is passed but never disposes the previous engine, leaking connection pools.

### C. Deprecations (Python 3.12 / FastAPI)

- **DEP-1 — `datetime.utcnow()`** (10 call sites) is deprecated in 3.12 and slated for removal.
- **DEP-2 — `@app.on_event("startup")`** (in both `main.py` and `dashboard/main.py`) is deprecated
  by FastAPI in favour of a lifespan handler. Both files also redundantly call `init_db()`.

### D. Performance

- **PERF-1 — `runs_today` loads all rows.** The dashboard counts today's runs with
  `len(session.exec(select(AgentRun)...).all())`, materialising every row instead of a SQL `COUNT`.

### E. Hygiene & documentation

- **DOC-1 — No `.gitignore`.** The system performs git commits of generated projects, yet there is
  no ignore file, so `agent_fleet.db`, `__pycache__/`, `.env`, and `projects/` would be committed.
- **DOC-2 — Stale docs.** `README.md` and `CLAUDE.md` describe only the 11-agent fleet (and claim
  "80+ tests"; there are 76). Neither mentions the Agent Studio layer (`core/agent_factory.py`,
  `core/guardrails.py`, `core/llm_providers.py`, the `Agent`/`AgentRun`/`ProviderConfig`/
  `GuardrailProfile` tables, or the `/studio`, `/agents/*`, `/settings/providers` routes).

### F. Test coverage

- **TEST-1 — Guardrails untested.** `core/guardrails.py` (injection, jailbreak, PII, toxicity,
  rate limit, composite input/output checks) has zero tests.
- **TEST-2 — Providers untested.** `core/llm_providers.py` (stub fallback, cost calc,
  `list_providers`) has zero tests.
- **TEST-3 — Agent Studio factory + routes untested.** `core/agent_factory.py` (heuristic fallback,
  slugify, auto-craft) and the new dashboard routes (manual create, run, delete) have zero tests.

---

## Checklist (actionable)

Implement in order; run `pytest tests/ -q` after each group and keep it green.

### Security & safety
- [x] **SEC-1** Default uvicorn bind to `127.0.0.1`, override via `HOST` env. (`main.py`, `.env.example`)
- [x] **SEC-2** Reject `/agents/{id}/run` input over a configurable max length. (`dashboard/main.py`)
- [x] **SEC-3** Clamp `temperature` to `[0, 2]` and `max_tokens` to `[1, 32000]` on manual create. (`dashboard/main.py`)
- [x] **SEC-4** Tighten the credit-card PII regex to a sane digit-run; cover with a test. (`core/guardrails.py`)

### Correctness / bugs
- [x] **BUG-1** Remove the duplicate `backoff` assignment. (`agents/base_agent.py`)
- [x] **BUG-2** Escalate `"blocked"` results to a human in the dispatcher. (`core/orchestrator.py`)
- [x] **BUG-3** Log the swallowed exception before falling back to the stub. (`core/llm_providers.py`)
- [x] **BUG-4** Dispose the previous engine when `get_engine` is re-pointed. (`core/state.py`)

### Deprecations
- [x] **DEP-1** Replace all `datetime.utcnow()` with a non-deprecated naive-UTC helper. (`core/state.py`, `core/orchestrator.py`, `dashboard/main.py`)
- [x] **DEP-2** Migrate both startup hooks to a FastAPI lifespan handler. (`dashboard/main.py`, `main.py`)

### Performance
- [x] **PERF-1** Count today's runs with `func.count`. (`dashboard/main.py`)

### Hygiene & docs
- [x] **DOC-1** Add a `.gitignore`.
- [x] **DOC-2** Refresh `README.md` and `CLAUDE.md` to document the Agent Studio layer and correct the test count.

### Test coverage
- [x] **TEST-1** Add `tests/test_guardrails.py`.
- [x] **TEST-2** Add `tests/test_llm_providers.py`.
- [x] **TEST-3** Add `tests/test_agent_studio.py` (factory + new dashboard routes).

---

## Out of scope (documented, not implemented here)

These are real gaps but are features/large refactors, not loophole fixes, and would jeopardise the
passing suite. Recorded for a future iteration:

- **Full pipeline orchestration.** `Orchestrator.run_project` only drives the BUILDING task loop;
  the clarification -> PRD -> architecture -> planning -> validation -> review -> docs state
  transitions are not wired in code (no automatic builder -> test_writer -> security_quality ->
  review chaining).
- **Authentication / multi-tenancy.** The product layer is "auth-free for demo"; `owner_email`
  columns exist but no login, session, or ownership enforcement exists.
- **SSE log streaming is a no-op.** `/projects/{id}/logs` tails
  `projects/{id}/logs/orchestrator.log`, which nothing ever writes.
- **SQLite under parallel writers.** Multiple concurrent agent tasks writing SQLite can hit
  `database is locked`; a real deployment needs Postgres or a write queue.
