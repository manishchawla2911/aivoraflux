# Workspace Foundation — Design

**Date:** 2026-05-31
**Status:** Approved for planning
**Cycle:** 1 of N (the "AI company" vision from `FEATURES_V1.md`)

## Context

`FEATURES_V1.md` describes a third product layer for NexaFlow AI: a **Workspace** (an
"AI company") that bundles a team of role-based agents who collaborate, share memory,
talk to the owner over chat platforms, and spawn sub-agents.

The existing app already ships two products on one FastAPI + SQLite process:

1. **The Fleet** — an orchestrator driving 11 specialized agents from client brief →
   deployed codebase (`core/orchestrator.py`, `agents/*`).
2. **Agent Studio** — user-crafted individual agents with tools, guardrails, providers,
   run console, and observability (`core/agent_factory.py`, `core/state.py::Agent`).

The full `FEATURES_V1.md` vision is too large for one cycle. It decomposes into seven
roughly-independent subsystems:

| # | Subsystem | Status |
|---|-----------|--------|
| A | Workspace + roster (create, pick/customize/name agents) | **This cycle** |
| B | Role-agent catalog (CEO, CFO, CTO, Marketing, COO) | **This cycle** |
| C | Shared workspace memory | **This cycle** |
| D | Internal agent↔agent chat | Deferred |
| E | External chat bridge (Telegram/WhatsApp/Slack, `/<agent>` routing) | Deferred |
| F | Voice marketing agent (STT/TTS + email + follow-up) | Deferred |
| G | CEO dynamic spawning + cost estimator | Deferred |

This document specs **A + B + C** — the foundation everything else attaches to.

## Goal & scope

Deliver: create a workspace, populate it with role-based agents from a code-defined
catalog (customizing name/emoji/prompt/model), a shared semantic memory backed by a
local ChromaDB, and **memory-aware runs** of individual members through the existing
run console.

**In scope**
- Workspace + member + memory data model.
- Code-defined role catalog of five C-suite/marketing roles.
- Pluggable embeddings abstraction (stub / sentence-transformers / Ollama).
- Memory store: canonical in SQLite, semantically indexed in ChromaDB, with SQL fallback.
- Workspace factory (create workspace → instantiate Agents + members → seed mission).
- Memory-aware extension of the existing `/agents/{id}/run` path.
- Workspace UI: list, creation wizard, detail (roster + memory feed + manual entry).

**Out of scope (later cycles)**
- Internal agent↔agent chat, external chat bridges, voice marketing, CEO dynamic
  spawning, cost estimator, automatic memory write-back from agent output.

## Key decisions

- **Agent relationship — option C.** `Workspace` + `WorkspaceMember` (join) that
  references the existing `Agent` row. Reuses all of Agent Studio's machinery
  (run console, guardrails, providers, observability) without overloading the `Agent`
  table; workspace-specific data lives on the member row.
- **Role catalog — code-defined constant** (`ROLE_CATALOG`), mirroring the existing
  `TOOL_CATALOG` precedent, keeping catalog in sync with the repo's "specs track code"
  philosophy. Five roles: CEO, CFO, CTO, Marketing, COO.
- **Memory shape — structured entries** (append-mostly) with authorship/kind/tags/history,
  canonical in SQLite; **ChromaDB (local persistent)** as the per-workspace semantic index.
- **Embeddings — thin pluggable seam** (`core/embeddings.py`) like `llm_providers`;
  deterministic stub for tests, local sentence-transformers default, Ollama option.
- **Memory-aware runs** — retrieval-into-context + manual curation; no auto-write-back yet.

## Data model (`core/state.py`)

Three new SQLModel tables, following existing conventions (`utcnow`, JSON-in-text
fields, string PKs, no cross-product FK on `owner_email` — matched by string).

### `Workspace`
- `id: str` (PK)
- `owner_email: Optional[str]` (indexed)
- `name: str`
- `company_description: Optional[str]`
- `mission: Optional[str]` (text)
- `status: str` = `"active"` — `active | archived`
- `created_at`, `updated_at: datetime` (default `utcnow`)

### `WorkspaceMember` (the join — option C)
- `id: str` (PK)
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `agent_id: str` (FK → `agent.id`, indexed)
- `role: str` — catalog role id (e.g. `ceo`)
- `display_name: Optional[str]`
- `order_index: int` = 0
- `created_at: datetime`

Holds only workspace-specific data; all runnable behavior stays on the reused `Agent`.

### `WorkspaceMemory` (canonical entry record)
- `id: str` (PK) — also the ChromaDB document id
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `author: Optional[str]` — member display name or `"owner"`
- `kind: str` — `goal | decision | fact | note`
- `content: str` (text)
- `tags: Optional[str]` — JSON list
- `created_at: datetime`

SQLite is the source of truth; the row `id` keys the embedding + content/metadata copy
in the workspace's Chroma collection.

### ER view
A rendered diagram lives at `docs/workspace-foundation-erd.html`.

```
Workspace 1───* WorkspaceMember *───1 Agent ───1 GuardrailProfile   (all existing on the right)
Workspace 1───* WorkspaceMemory  (id == ChromaDB doc id, per-workspace collection)
```

## Role catalog (`core/workspace_roles.py`)

A `ROLE_CATALOG` constant mirroring `TOOL_CATALOG`. Five roles: **CEO, CFO, CTO,
Marketing, COO**. Each entry:

- `id` — e.g. `ceo`
- `label` — display name
- `avatar_emoji`
- `category` — maps to an existing `agent_factory.CATEGORIES` value
- `default_system_prompt` — concrete role + responsibilities
- `suggested_tools` — ids from `agent_factory.TOOL_CATALOG`
- `default_model` — provider/model defaults

Marketing ships as a plain text agent this cycle (voice/email powers arrive in cycle F).

## Embeddings (`core/embeddings.py`)

One entry point `embed(texts: list[str]) -> list[list[float]]`, backend chosen by
`EMBEDDING_BACKEND` env:

- `stub` (default in tests) — deterministic hash-based vectors, no network, no deps.
- `sentence_transformers` — local `all-MiniLM-L6-v2`, prod default (guarded import).
- `ollama` — local Ollama embeddings endpoint (`OLLAMA_BASE_URL`, model via
  `OLLAMA_EMBED_MODEL`, e.g. `nomic-embed-text`).

Real-backend failures fall back to the stub with a logged warning (same pattern as
`llm_providers._complete_ollama`). A small `get_embedding_function()` adapter hands
Chroma whatever backend is selected.

## Memory store (`core/workspace_memory.py`)

Wraps both stores behind a small API:

- `add_entry(session, workspace_id, author, kind, content, tags) -> WorkspaceMemory`
  — writes the SQLite row **and** upserts into the workspace's Chroma collection.
  Chroma side is **fail-open** (a broken write never propagates), like
  `observability.record_event`.
- `query(session, workspace_id, text, k=5, kind=None) -> list[WorkspaceMemory]`
  — semantic top-k from Chroma hydrated back to SQLite rows; falls back to recent-rows
  SQL filtering if Chroma is unavailable.
- `build_memory_context(session, workspace_id, query_text, k) -> str`
  — formats mission + top-k entries (tagged author/kind) into a compact
  "Shared workspace memory" block. Lives here (not the route) so it is unit-testable.

Chroma runs in local persistent mode under `CHROMA_DIR` (default `./chroma`), one
collection per workspace.

## Workspace factory (`core/workspace_factory.py`)

Pure functions; no web coupling beyond a passed `Session` (mirrors `agent_factory`).

`create_workspace(session, owner_email, name, company_description, mission,
selected_roles, member_overrides) -> Workspace`:

1. Insert the `Workspace` row.
2. For each selected role: build an `AgentSpec` from `ROLE_CATALOG[role]` applying the
   owner's per-role overrides, insert an `Agent` row (reuse `AgentSpec.to_db_kwargs`),
   then insert a `WorkspaceMember` linking them with `role` + `order_index`.
3. Seed `mission` as the first `WorkspaceMemory` entry (`author="owner"`, `kind="goal"`)
   via `workspace_memory.add_entry` — so retrieval has content from minute one.

`member_overrides` is a dict per role (`display_name`, `avatar_emoji`, `system_prompt`,
`model_provider`, `model_name`); omitted fields fall back to the template.

## Memory-aware runs (extend `dashboard/main.py::run_agent`)

Smallest possible change to the proven run path:

1. Before building the `CompletionRequest`, look up whether `agent_id` is a
   `WorkspaceMember`. If not → behaves exactly as today (zero change for Studio agents).
2. If it is → call `workspace_memory.build_memory_context(...)` and prepend the
   "Shared workspace memory" block to the system prompt. `K` via `WORKSPACE_MEMORY_K`
   env, default 5.
3. Everything downstream (guardrails, `complete`, `_save_run`, `record_event`) untouched.

## Routes & templates (`dashboard/main.py`)

| Route | Purpose |
|-------|---------|
| `GET /workspaces` | list owner's workspaces (name, member count, mission snippet) |
| `GET /workspaces/new` | creation wizard — name/description/mission + role picker with per-role customization |
| `POST /workspaces` | create via factory, redirect to detail |
| `GET /workspaces/{id}` | detail — roster (each member links to its `/agents/{id}` console), mission, memory feed |
| `POST /workspaces/{id}/memory` | owner manually adds a memory entry (`kind`, `content`, `tags`) |
| `POST /workspaces/{id}/archive` | set status `archived` |

New templates: `workspaces.html`, `workspace_new.html`, `workspace_detail.html`, plus a
"Workspaces" nav link in `_base.html`. Member runs reuse the existing `agent.html`
console — no new run UI.

## Config & dependencies

- `requirements.txt`: add `chromadb`; `sentence-transformers` optional (guarded import).
- New env (documented in `.env.example` + `CLAUDE.md`): `CHROMA_DIR` (`./chroma`),
  `EMBEDDING_BACKEND` (`stub | sentence_transformers | ollama`), `OLLAMA_EMBED_MODEL`,
  `WORKSPACE_MEMORY_K` (default 5).

## Testing (`tests/test_workspaces.py`)

One file per the repo convention. All offline, `EMBEDDING_BACKEND=stub`, no API keys.

- **embeddings** — stub deterministic & correctly dimensioned; backend selection honors env.
- **memory store** — `add_entry` writes SQLite + Chroma; `query` returns semantically
  nearest stub entries; **SQL fallback** when Chroma is monkeypatched to fail.
- **factory** — `create_workspace` creates N `Agent` + N `WorkspaceMember` rows + seeds
  the mission memory; overrides apply; omitted fields fall back to template.
- **role catalog** — all five roles well-formed (required keys, valid category,
  non-empty prompt, tools exist in `TOOL_CATALOG`).
- **routes** — `TestClient`: create workspace → detail shows roster + mission; add memory
  entry; **memory-aware run** injects the memory block (asserted via an injected fake
  completion that echoes its system prompt) while a non-workspace Studio agent's run is
  unchanged.

## File summary

**New:** `core/workspace_roles.py`, `core/embeddings.py`, `core/workspace_memory.py`,
`core/workspace_factory.py`, `dashboard/templates/{workspaces,workspace_new,workspace_detail}.html`,
`tests/test_workspaces.py`.

**Edited:** `core/state.py` (3 tables), `dashboard/main.py` (routes + memory-aware run +
nav), `requirements.txt`, `.env.example`, `CLAUDE.md` (document the new layer).

## Invariants preserved

- New project-state machinery is **not** touched; this is a Studio-side product layer.
- Studio agent runs are byte-for-byte unchanged unless the agent is a workspace member.
- Chroma and embedding failures are **fail-open** — they never break a run or a write.
- Tests stay offline and key-free (`EMBEDDING_BACKEND=stub`, injected fake completion).
