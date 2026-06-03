# Internal Agent↔Agent Chat (Subsystem D) — Design

**Date:** 2026-05-31
**Status:** Approved for planning
**Cycle:** 2 of the `FEATURES_V1.md` "AI company" vision (D of D/E/F/G)

## Context

The Workspace foundation (cycle 1) shipped: workspaces, a role-agent catalog (CEO,
CFO, CTO, Marketing, COO), ChromaDB-backed shared memory, and memory-aware single
runs of a member through `/agents/{id}/run`. See
`docs/superpowers/specs/2026-05-31-workspace-foundation-design.md`.

`FEATURES_V1.md` asks for an internal section where workspace agents "talk to each
other" and the owner can converse with them. The remaining vision decomposes into four
independent cycles, built in dependency order: **D internal chat → G CEO spawning +
cost estimator → E external chat bridge → F voice marketing.** This document specs **D**.

## Goal & scope

A single internal chat channel per workspace. The owner or an agent posts a message;
an agent replies only when **@mentioned** (or addressed via `/<name>`). An agent's reply
may @mention other agents, triggering a **bounded auto-cascade** of further replies.
Every agent turn is grounded in shared memory + recent chat history and runs through the
same guardrail-wrapped completion path as a normal agent run. The owner can **pin** any
chat message into shared memory.

**In scope**
- `WorkspaceChatMessage` data model (single channel per workspace).
- Mention parsing/resolution against member display names and agent slugs.
- Bounded, loop-guarded cascade engine.
- Read-from-memory per turn; manual pin-to-memory.
- A reusable completion helper shared by the run endpoint and chat (refactor to remove
  guardrail-logic duplication).
- Chat routes + a dedicated chat UI; a link from the workspace detail page.

**Out of scope (later cycles)**
- Meeting/discussion mode (turn-based multi-round facilitation).
- Auto/CEO-summarized memory write-back.
- External chat bridges (Telegram/WhatsApp/Slack) — cycle E.
- Voice, email, follow-up campaigns — cycle F.
- Dynamic agent spawning / cost estimator — cycle G.
- Multiple channels/threads; streaming responses.

## Key decisions

- **Conversation model — mention-driven single channel.** One channel per workspace.
  An agent responds only when @mentioned; deterministic and offline-testable.
- **Agent-to-agent — bounded auto-cascade.** An agent reply that @mentions another
  agent triggers that agent, capped by a turn budget and a per-cascade visited-set
  (no member triggered twice → no ping-pong / infinite loop).
- **Memory — read + manual pin.** Turns read shared memory; nothing is auto-written.
  The owner can pin a message into `WorkspaceMemory`. Keeps memory clean.
- **Reuse over duplication.** The guardrail→complete→save core of `run_agent` is
  extracted into a shared helper so chat and the run endpoint behave identically.

## Data model (`core/state.py`)

### `WorkspaceChatMessage`
- `id: str` (PK)
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `author_kind: str` — `owner | agent`
- `author_member_id: Optional[str]` — `WorkspaceMember.id` when an agent authored it;
  `None` for owner
- `author_name: str` — display label (`"Owner"` or the member's display name)
- `content: str`
- `mentions: Optional[str]` — JSON list of resolved member ids this message addresses
- `triggered_by_id: Optional[str]` — id of the message that caused this one (`None` for
  owner posts); enables cascade rendering/tracing
- `pinned_memory_id: Optional[str]` — set when pinned into `WorkspaceMemory`
- `created_at: datetime` (default `_utcnow`)

Single channel per workspace (no channel/thread column this cycle).

## Shared completion helper (`core/agent_runtime.py`)

Extract the inline guardrail→complete→save logic from `dashboard/main.py::run_agent`
into a reusable function so chat and the run endpoint share one code path:

```
run_agent_completion(session, agent, *, user_input, extra_system="") -> RunResult
```

- Builds the system prompt as `f"{extra_system}\n\n{agent.system_prompt}"` when
  `extra_system` is non-empty, else `agent.system_prompt`.
- Applies input guardrails; on block, records the run and returns a blocked `RunResult`
  (no exception).
- Calls `complete(req, agent.model_provider)`; applies output guardrails (redaction).
- Persists via the existing `_save_run` + observability path.
- Returns `RunResult(final_text, verdict, latency_ms, input_tokens, output_tokens,
  cost_usd, blocked: bool)`.

`run_agent` becomes a thin caller passing the memory block as `extra_system`.
`workspace_chat._run_turn` calls the same helper. Guardrails/observability stay
identical everywhere; no logic is copied into chat.

Note: `_save_run` and the guardrail wiring currently live in `dashboard/main.py`. The
refactor moves the reusable completion core into `core/agent_runtime.py`; if `_save_run`
must move with it, keep a thin re-export in `dashboard/main.py` so existing imports/tests
do not break. The implementation plan will pin the exact seam.

## Chat engine (`core/workspace_chat.py`)

Pure logic over a passed `Session`; no web coupling.

- `resolve_mentions(text, members) -> list[str]` — match `@name` / `/name` tokens
  against each member's `display_name` and the underlying `Agent.slug`, case-insensitive,
  **longest-match-first** (so "Marketing Lead" beats "Marketing"). Returns resolved
  member ids, de-duplicated, order-preserved. Unmatched tokens are ignored.
- `post_message(session, workspace_id, *, author_kind, content, author_member_id=None)
  -> list[WorkspaceChatMessage]` — persists the originating message, resolves its
  mentions, runs the cascade, returns all newly-created messages (including the original).
- `_run_turn(session, workspace_id, member, trigger_msg) -> WorkspaceChatMessage` —
  builds context = `build_memory_context(...)` + last `CHAT_HISTORY_N` messages as a
  transcript; calls `run_agent_completion(...)` with that context as `extra_system`;
  persists the reply with `triggered_by_id = trigger_msg.id` and resolved mentions. A
  guardrail-blocked turn still produces a (blocked-notice) agent message rather than
  raising.
- **Cascade loop** — a queue seeded with the originating message's mentions; each popped
  member → one `_run_turn` → that reply's newly-mentioned members enqueued. Bounded by
  `WORKSPACE_CHAT_MAX_TURNS` (default 6) and a per-cascade visited-set (each member fires
  at most once per cascade). On budget exhaustion, remaining mentions are dropped and
  logged — never executed.

## Routes (`dashboard/main.py`)

| Route | Purpose |
|-------|---------|
| `GET /workspaces/{id}/chat` | render transcript + composer (404 if workspace missing) |
| `POST /workspaces/{id}/chat` | owner posts `content`; runs cascade; 303 back to chat |
| `POST /workspaces/{id}/chat/{msg_id}/pin` | pin message into `WorkspaceMemory` (kind defaults to `decision`); set `pinned_memory_id`; 303 |

(A `GET …/chat/api/messages` JSON endpoint was considered but dropped this cycle — the
HTML view re-renders on POST, so polling has no consumer until a streaming UI lands.)

## UI

`dashboard/templates/workspace_chat.html` (extends `_base.html`): transcript with owner
messages right-aligned and agent messages left-aligned with avatar + role; cascade
replies visually indented under their `triggered_by` message; a composer textarea with a
hint that `@Name` / `/name` addresses an agent; a "📌 Pin to memory" control on each agent
message. `workspace_detail.html` gains a "Team chat →" link. No streaming this cycle —
POST runs the bounded cascade and re-renders.

## Config

New env (documented in `.env.example` + `CLAUDE.md`):
- `WORKSPACE_CHAT_MAX_TURNS` (default 6) — cascade turn budget.
- `CHAT_HISTORY_N` (default 12) — recent messages included as turn context.

## Testing (`tests/test_workspace_chat.py`)

All offline, `EMBEDDING_BACKEND=stub`, completion monkeypatched (reuse the foundation
tests' echo-fake / client fixtures).

- **mention resolution** — `@CEO`/`/ceo` resolve to the right member; longest-match
  wins; unknown tokens ignored; results de-duplicated.
- **single mention → single reply** — owner @mentions CEO → exactly one agent message,
  authored by that member; assert the memory block + transcript reached the system prompt
  via the echo-fake.
- **bounded cascade** — fake completion that always emits `@OtherAgent`; assert total
  agent turns ≤ `WORKSPACE_CHAT_MAX_TURNS` and no member fires twice.
- **no-mention post** — owner message with no mention → owner message persisted, zero
  agent replies.
- **pin** — pinning creates a `WorkspaceMemory` row, sets `pinned_memory_id`, and the
  pinned content is retrievable via `workspace_memory.query`.
- **shared-helper parity / regression** — a non-chat `/agents/{id}/run` is unchanged
  after the refactor; a guardrail-blocked chat input yields a blocked agent message,
  not an exception.
- **routes** — `TestClient`: post to chat → transcript shows owner + reply; pin route
  redirects and persists.

## File summary

**New:** `core/workspace_chat.py`, `core/agent_runtime.py`,
`dashboard/templates/workspace_chat.html`, `tests/test_workspace_chat.py`.

**Edited:** `core/state.py` (1 table), `dashboard/main.py` (chat routes; `run_agent`
slimmed to use the shared helper), `dashboard/templates/workspace_detail.html` (link),
`.env.example`, `CLAUDE.md`.

## Invariants preserved

- Studio agent runs remain unchanged (same guardrails/observability via the shared helper).
- Chat turns are bounded and loop-guarded; a cascade can never run unbounded.
- Memory writes stay manual/curated; chat never auto-pollutes memory.
- Fully offline-testable: no external services; completion + embeddings stubbed.
- Project-state machine and fleet pipeline are untouched (this is a Studio-side layer).
