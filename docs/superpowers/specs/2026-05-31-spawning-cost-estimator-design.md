# CEO Dynamic Spawning + Cost Estimator (Subsystem G) — Design

**Date:** 2026-05-31
**Status:** Approved (owner pre-delegated all decisions for this run)
**Cycle:** 3 of the `FEATURES_V1.md` "AI company" vision (G of D/G/E/F)

## Context

Cycles 1–2 shipped the Workspace foundation (roster, role catalog, ChromaDB shared
memory, memory-aware runs) and internal agent chat (mention-driven bounded cascade,
shared `run_agent_completion`). This cycle implements `FEATURES_V1.md` items 6–7:

> "The CEO can spawn new agents, such as a project manager agent per project, which will
> spawn different developer or audit agents based on the task."
> "A cost estimator agent will estimate cost on the initiation of the project and that
> will be reported to the client."

## Goal & scope

A workspace can open **projects**. Opening a project deterministically **spawns a team**:
the CEO spawns a Project Manager (PM) for the project, and the PM spawns developer and
auditor agents sized to the brief. At initiation a **cost estimator** produces a
client-facing quote from the planned team and `PROVIDER_CATALOG` pricing. The spawn
hierarchy (CEO → PM → devs/auditors) is recorded and shown in the UI.

**In scope**
- `WorkspaceProject` data model + a self-referential spawn hierarchy on `WorkspaceMember`
  (`parent_member_id`, `origin`).
- A code-defined `SPAWN_ROLE_CATALOG` (project_manager, developer, auditor).
- `spawn_member(...)` — instantiate one spawnable role as a real `Agent` + `WorkspaceMember`.
- A deterministic `plan_team(brief)` and `spawn_project_team(...)` orchestration.
- A deterministic `cost_estimator.estimate_project_cost(...)` quote, stored on the project.
- Routes + UI: project list, create (brief → estimate + spawn), and detail (quote + team tree).

**Out of scope (deferred / other cycles)**
- LLM-autonomous open-ended spawning (team sizing is deterministic this cycle; an LLM
  planner with a deterministic fallback is a later enhancement).
- Real execution of spawned dev/audit agents against a codebase (that is the existing
  fleet pipeline's job; here spawning creates the *team*, not a build run).
- External chat bridges (E) and voice (F).
- Billing/payment — the quote is informational.

## Key decisions

- **Projects are first-class.** A `WorkspaceProject` row anchors the brief, the spawned
  PM, the team, and the cost quote. This gives spawning and estimation a clear home and a
  natural UI.
- **Spawning reuses the agent machinery.** Spawned members are ordinary `Agent` rows +
  `WorkspaceMember` links (via the same `AgentSpec.to_db_kwargs` path as
  `workspace_factory`), so they inherit run console, guardrails, chat, and observability
  for free. A shared `instantiate_member(...)` helper is extracted from
  `workspace_factory` so seeding and spawning don't duplicate creation logic.
- **Hierarchy via a self-FK.** `WorkspaceMember.parent_member_id` records CEO→PM→dev/audit.
  `origin` (`seed | spawned`) distinguishes catalog roster members from runtime spawns.
- **Deterministic, offline-first.** `plan_team` and `estimate_project_cost` are pure
  deterministic functions (no LLM, no network) so the whole cycle is fast and testable.
  Cost uses real `PROVIDER_CATALOG` per-million-token rates.

## Data model (`core/state.py`)

### `WorkspaceProject` (new)
- `id: str` (PK)
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `name: str`
- `brief: str`
- `client_name: Optional[str]`
- `status: str` = `"estimating"` — `estimating | staffed | archived`
- `pm_member_id: Optional[str]` — the spawned PM's `WorkspaceMember.id`
- `estimate_json: Optional[str]` — JSON `CostEstimate` (quote shown to client)
- `created_at`, `updated_at: datetime`

### `WorkspaceMember` (add two columns)
- `parent_member_id: Optional[str] = None` — the member that spawned this one
- `origin: str = "seed"` — `seed` (from the role catalog) | `spawned` (runtime)

(Additive nullable columns; new DBs get them via `create_all`. Consistent with how prior
cycles extended the schema; no migration framework in this demo app.)

## Spawn role catalog (`core/workspace_spawn.py`)

`SPAWN_ROLE_CATALOG` mirrors `ROLE_CATALOG`'s shape (id, label, avatar_emoji, category,
default_model, suggested_tools, default_system_prompt). Three roles:
- `project_manager` — plans and coordinates a project's delivery.
- `developer` — implements assigned tasks.
- `auditor` — reviews work for quality/security/compliance.

`get_spawn_role(role_id) -> Optional[Dict]`.

## Member instantiation refactor (`core/workspace_factory.py`)

Extract the per-role "build AgentSpec → persist Agent → persist WorkspaceMember" body of
`create_workspace` into:

```
instantiate_member(session, workspace_id, role_def, *, display_name=None,
                   system_prompt=None, model_provider=None, model_name=None,
                   avatar_emoji=None, order_index=0, parent_member_id=None,
                   origin="seed", guardrail_id, owner_email) -> WorkspaceMember
```

`create_workspace` calls it with `origin="seed"`; spawning calls it with
`origin="spawned"` and a `parent_member_id`. One creation path, no duplication.
`role_def` is any catalog dict (from `ROLE_CATALOG` or `SPAWN_ROLE_CATALOG`).

## Spawning engine (`core/workspace_spawn.py`)

- `spawn_member(session, workspace_id, role_id, *, parent_member_id=None,
  display_name=None) -> WorkspaceMember` — look up `SPAWN_ROLE_CATALOG[role_id]` (return
  `None`/raise on unknown), resolve the Standard guardrail profile, call
  `instantiate_member(..., origin="spawned")` with the next `order_index`.
- `plan_team(brief: str) -> TeamPlan` — deterministic: `num_developers` scales with brief
  length (1–3), `with_auditor` True when the brief mentions audit/security/compliance/
  review (else still True by default for safety — final rule pinned in the plan).
- `spawn_project_team(session, workspace_id, project, *, plan=None) -> dict` —
  1. find the CEO member (role `ceo`, `origin="seed"`); its id is the PM's parent (or
     `None` if absent).
  2. spawn a PM (`parent_member_id = CEO`), set `project.pm_member_id`.
  3. `plan = plan or plan_team(project.brief)`; spawn `num_developers` developers and an
     auditor (when `with_auditor`) with `parent_member_id = PM`.
  4. return `{"pm": pm, "developers": [...], "auditors": [...]}`.

## Cost estimator (`core/cost_estimator.py`)

Pure deterministic quote, no LLM/network.

- `model_rates(provider, model) -> (in_rate, out_rate)` — look up USD/M-token rates in
  `llm_providers.PROVIDER_CATALOG`; fall back to Anthropic Sonnet rates if unknown.
- `estimate_project_cost(brief, team_size, *, provider="anthropic",
  model="claude-sonnet-4-6", assumed_turns=8) -> CostEstimate`:
  - `brief_tokens = len(brief)//4`
  - per turn: `input ≈ BASE_CONTEXT(1500) + brief_tokens`, `output ≈ OUT_PER_TURN(500)`
  - totals scaled by `team_size * assumed_turns`
  - `cost_usd = input/1e6*in_rate + output/1e6*out_rate`
  - returns `CostEstimate(input_tokens, output_tokens, cost_usd, team_size, provider,
    model, assumptions: dict)` with `to_json()`/`from_json()` helpers.

## Routes & UI (`dashboard/main.py`)

| Route | Purpose |
|-------|---------|
| `GET /workspaces/{id}/projects` | list projects with status + quote |
| `POST /workspaces/{id}/projects` | create project (name, brief, client_name) → estimate + spawn team → redirect to detail |
| `GET /workspaces/{id}/projects/{pid}` | detail: brief, **client quote** (cost breakdown), spawned team tree (CEO → PM → devs/auditors) |
| `POST /workspaces/{id}/projects/{pid}/archive` | archive a project |

`POST /projects` flow: insert `WorkspaceProject` (status `estimating`), compute
`estimate_project_cost`, store `estimate_json`, run `spawn_project_team`, set status
`staffed`, seed a memory entry (`kind="decision"`, the project + quote) so the company
stays aligned, redirect to detail.

New templates: `workspace_projects.html`, `workspace_project_new.html`,
`workspace_project_detail.html`. The workspace detail page gains a "Projects →" link.

## Config

New env (documented in `.env.example` + `CLAUDE.md`):
- `COST_ASSUMED_TURNS` (default 8), `COST_BASE_CONTEXT_TOKENS` (default 1500),
  `COST_OUTPUT_TOKENS_PER_TURN` (default 500).

## Testing (`tests/test_workspace_spawn.py`)

All offline, `EMBEDDING_BACKEND=stub`, completion not even needed (spawning/estimation are
deterministic).
- **spawn catalog** — three roles well-formed (keys, valid category, tools ⊆ TOOL_CATALOG).
- **instantiate_member refactor** — `create_workspace` still produces identical seed
  members (cycle-1 tests stay green); a direct `instantiate_member` call sets
  `origin`/`parent_member_id`.
- **spawn_member** — creates an Agent + a `WorkspaceMember` with `origin="spawned"`,
  correct role, parent link, next order_index; unknown role rejected.
- **plan_team** — deterministic counts for short vs long briefs; auditor rule honored.
- **spawn_project_team** — CEO→PM parent link; PM→developers/auditor parent links;
  `project.pm_member_id` set; team counts match the plan.
- **cost estimator** — deterministic totals for known inputs; rate lookup + fallback;
  `to_json`/`from_json` round-trip.
- **routes** — `TestClient`: create project → detail shows the quote and the team tree;
  archive flips status; project under a workspace with no CEO still spawns (PM parent None).

## File summary

**New:** `core/workspace_spawn.py`, `core/cost_estimator.py`,
`dashboard/templates/workspace_projects.html`, `workspace_project_new.html`,
`workspace_project_detail.html`, `tests/test_workspace_spawn.py`.

**Edited:** `core/state.py` (1 table + 2 columns), `core/workspace_factory.py`
(extract `instantiate_member`), `dashboard/main.py` (project routes),
`dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

## Invariants preserved

- Spawned agents are ordinary Studio agents — guardrailed, runnable, observable, chat-able.
- `create_workspace` seed behavior is byte-identical after the `instantiate_member` extract
  (cycle-1 tests are the regression guard).
- Spawning and estimation are deterministic, bounded, offline — no runaway agent creation
  (team size is plan-capped), no external calls.
- Memory stays curated (one decision entry per project initiation, not per spawn).
