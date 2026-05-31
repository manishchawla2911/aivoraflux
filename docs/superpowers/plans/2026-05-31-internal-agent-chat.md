# Internal Agent Chat (Subsystem D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single internal chat channel per workspace where the owner and agents talk; an agent replies when @mentioned, an agent's reply can @mention others to trigger a bounded auto-cascade, every turn is grounded in shared memory, and the owner can pin a message into shared memory.

**Architecture:** A new `core/workspace_chat.py` engine (mention resolution + bounded cascade) drives turns through a shared `core/agent_runtime.py::run_agent_completion` helper — extracted from the existing `/agents/{id}/run` so chat and the run endpoint use one guardrailed completion path. One new SQLModel table `WorkspaceChatMessage`. Chat routes + a dedicated chat template hang off the existing workspace UI. No external services; fully offline-testable.

**Tech Stack:** Python 3, SQLModel/SQLite, FastAPI + Jinja2, pytest (`asyncio_mode=auto`). Reuses cycle-1 modules: `workspace_memory.build_memory_context`, `workspace_factory.create_workspace`, the role catalog, and the stub embeddings backend.

**Spec:** `docs/superpowers/specs/2026-05-31-internal-agent-chat-design.md`

**Conventions to honor:**
- `core.state.utcnow()`, never `datetime.utcnow()`. String PKs, JSON-in-text fields.
- Fail-open telemetry (`record_event`), no `extra=` with a `message` key in logging.
- Tests run offline, no API keys: `EMBEDDING_BACKEND=stub`, completion monkeypatched.
- After each task: `pytest tests/ -v` (full suite) must stay green.

---

## File Structure

**New files**
- `core/agent_runtime.py` — `RunResult` + `run_agent_completion(...)`: the shared guardrail→complete→persist core.
- `core/workspace_chat.py` — `resolve_mentions`, `post_message`, `_run_turn`, cascade loop.
- `dashboard/templates/workspace_chat.html` — transcript + composer.
- `tests/test_workspace_chat.py` — all subsystem-D tests.

**Modified files**
- `core/state.py` — add `WorkspaceChatMessage` table.
- `dashboard/main.py` — slim `run_agent` to call the shared helper; add chat routes; drop now-unused imports.
- `dashboard/templates/workspace_detail.html` — add "Team chat →" link.
- `tests/test_workspaces.py` — repoint the `client` fixture's completion patch to the new seam.
- `.env.example`, `CLAUDE.md` — document chat env + the new layer.

---

## Task 1: WorkspaceChatMessage table

**Files:**
- Modify: `core/state.py` (add table after `WorkspaceMemory`, before the observability banner)
- Test: `tests/test_workspace_chat.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `tests/test_workspace_chat.py`:

```python
"""Tests for internal agent chat (subsystem D)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceMemory, WorkspaceChatMessage,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'chat.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_chat_message_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(WorkspaceChatMessage(
            id="c1", workspace_id="w1", author_kind="owner",
            author_name="Owner", content="hello @CEO", mentions='["m1"]',
        ))
        s.commit()
    with Session(db) as s:
        rows = s.exec(select(WorkspaceChatMessage).where(
            WorkspaceChatMessage.workspace_id == "w1")).all()
        assert len(rows) == 1
        assert rows[0].author_kind == "owner"
        assert rows[0].triggered_by_id is None
        assert rows[0].pinned_memory_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_chat.py::test_chat_message_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'WorkspaceChatMessage' from 'core.state'`

- [ ] **Step 3: Add the table**

In `core/state.py`, immediately AFTER the `WorkspaceMemory` class and BEFORE the
`# Observability` banner, add:

```python
class WorkspaceChatMessage(SQLModel, table=True):
    """One message in a workspace's single internal chat channel."""
    __tablename__ = "workspace_chat_message"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    author_kind: str = "owner"                  # owner | agent
    author_member_id: Optional[str] = None      # WorkspaceMember.id when agent-authored
    author_name: str = "Owner"                  # display label in the transcript
    content: str = ""
    mentions: Optional[str] = None              # JSON list of resolved member ids
    triggered_by_id: Optional[str] = None       # message id that caused this one
    pinned_memory_id: Optional[str] = None      # WorkspaceMemory.id once pinned
    created_at: datetime = Field(default_factory=_utcnow)
```

(`Field`, `Optional`, `datetime`, `_utcnow`, `SQLModel` are already imported in the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_chat.py::test_chat_message_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/test_workspace_chat.py
git commit -m "feat(chat): add WorkspaceChatMessage table"
```

---

## Task 2: Shared completion helper + run_agent refactor

**Files:**
- Create: `core/agent_runtime.py`
- Modify: `dashboard/main.py` (`run_agent` body; remove now-unused imports/`_save_run`)
- Modify: `tests/test_workspaces.py` (repoint the `client` fixture's completion patch)
- Test: `tests/test_workspace_chat.py` (append)

### Background (read before editing)

`dashboard/main.py::run_agent` currently inlines: input guardrail → build prompt →
`complete(req, provider)` → output guardrail → `_save_run` (which inserts `AgentRun` and
calls `record_event`). `_save_run` is only used by `run_agent`. The cycle-1 `client`
fixture in `tests/test_workspaces.py` patches `dashboard.main.complete`. We are moving the
completion call into `core/agent_runtime.py`, so the patch target must move to
`core.llm_providers.complete` (a single module-attribute seam both callers share).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_chat.py`:

```python
from core import workspace_factory as wf
from core.agent_runtime import run_agent_completion
from core.llm_providers import CompletionResponse


def _echo_complete(req, provider):
    return CompletionResponse(text=f"SYS:::{req.system}", provider=provider,
                              model=req.model, stubbed=True)


def test_run_agent_completion_passes_and_persists(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    with Session(db) as s:
        s.add(Agent(id="a1", name="Solo", system_prompt="You are solo.",
                    model_name="m", model_provider="anthropic"))
        s.commit()
        agent = s.get(Agent, "a1")
        result = run_agent_completion(s, agent, user_input="hi", extra_system="MEMCTX")
        assert result.blocked is False
        assert result.verdict == "passed"
        # extra_system is prepended to the system prompt
        assert result.final_text == "SYS:::MEMCTX\n\nYou are solo."
        # a run row was persisted
        from core.state import AgentRun
        runs = s.exec(select(AgentRun).where(AgentRun.agent_id == "a1")).all()
        assert len(runs) == 1 and runs[0].guardrail_verdict == "passed"


def test_run_agent_completion_no_extra_system(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    with Session(db) as s:
        s.add(Agent(id="a2", name="Solo", system_prompt="Bare.",
                    model_name="m", model_provider="anthropic"))
        s.commit()
        agent = s.get(Agent, "a2")
        result = run_agent_completion(s, agent, user_input="hi")
        assert result.final_text == "SYS:::Bare."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_chat.py -k run_agent_completion -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.agent_runtime'`

- [ ] **Step 3: Create `core/agent_runtime.py`**

```python
"""Shared agent completion core: guardrails → complete → persist + telemetry.

Extracted from dashboard.main.run_agent so the run endpoint AND internal chat
drive one identical, guardrailed path. Calls llm_providers.complete via the module
attribute so a single monkeypatch point (core.llm_providers.complete) covers both
callers in tests.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session

from core import llm_providers
from core.guardrails import Guardrails
from core.llm_providers import CompletionRequest
from core.observability import record_event
from core.state import Agent, AgentRun, GuardrailProfile


@dataclass
class RunResult:
    final_text: str
    verdict: str                    # passed | blocked
    blocked: bool
    block_stage: Optional[str]      # input | output | None
    reason: Optional[str]
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stubbed: bool
    provider: str
    model: str


def _persist_run(session: Session, agent: Agent, inp: str, out: str, *,
                 verdict: str, reason: Optional[str], latency: int,
                 in_tok: int, out_tok: int, cost: float) -> None:
    session.add(AgentRun(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        input_text=inp,
        output_text=out,
        latency_ms=latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        guardrail_verdict=verdict,
        guardrail_reason=reason,
    ))
    session.commit()
    # Fail-open telemetry mirror (no raw text).
    record_event(
        event_type="guardrail_block" if verdict == "blocked" else "run",
        source="studio",
        status="blocked" if verdict == "blocked" else "passed",
        agent_id=agent.id,
        agent_name=agent.name,
        agent_type=agent.category,
        guardrail_verdict=verdict,
        error=reason,
        duration_ms=latency,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        input_chars=len(inp or ""),
        output_chars=len(out or ""),
    )


def run_agent_completion(session: Session, agent: Agent, *, user_input: str,
                         extra_system: str = "",
                         profile: Optional[GuardrailProfile] = None) -> RunResult:
    """Run one guardrailed completion for `agent`, persisting the run. Never raises
    on a guardrail block — returns a blocked RunResult instead."""
    t0 = time.time()
    if profile is None and agent.guardrail_profile_id:
        profile = session.get(GuardrailProfile, agent.guardrail_profile_id)
    rails = Guardrails(profile=profile)

    in_verdict = rails.check_input(agent.id, user_input)
    if not in_verdict.allowed:
        latency = int((time.time() - t0) * 1000)
        _persist_run(session, agent, user_input, "", verdict="blocked",
                     reason=in_verdict.reason, latency=latency, in_tok=0, out_tok=0, cost=0.0)
        return RunResult(final_text="", verdict="blocked", blocked=True,
                         block_stage="input", reason=in_verdict.reason, latency_ms=latency,
                         input_tokens=0, output_tokens=0, cost_usd=0.0, stubbed=False,
                         provider="", model=agent.model_name)

    system_prompt = f"{extra_system}\n\n{agent.system_prompt}" if extra_system else agent.system_prompt
    req = CompletionRequest(
        system=system_prompt,
        user=user_input,
        model=agent.model_name,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
    resp = llm_providers.complete(req, agent.model_provider)

    out_verdict = rails.check_output(resp.text)
    if not out_verdict.allowed:
        _persist_run(session, agent, user_input, resp.text, verdict="blocked",
                     reason=out_verdict.reason, latency=resp.latency_ms,
                     in_tok=resp.input_tokens, out_tok=resp.output_tokens, cost=resp.cost_usd)
        return RunResult(final_text="", verdict="blocked", blocked=True,
                         block_stage="output", reason=out_verdict.reason,
                         latency_ms=resp.latency_ms, input_tokens=resp.input_tokens,
                         output_tokens=resp.output_tokens, cost_usd=resp.cost_usd,
                         stubbed=resp.stubbed, provider=resp.provider, model=resp.model)

    final_text = out_verdict.redacted_text or resp.text
    _persist_run(session, agent, user_input, final_text, verdict="passed", reason=None,
                 latency=resp.latency_ms, in_tok=resp.input_tokens,
                 out_tok=resp.output_tokens, cost=resp.cost_usd)
    return RunResult(final_text=final_text, verdict="passed", blocked=False,
                     block_stage=None, reason=None, latency_ms=resp.latency_ms,
                     input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                     cost_usd=resp.cost_usd, stubbed=resp.stubbed,
                     provider=resp.provider, model=resp.model)
```

- [ ] **Step 4: Refactor `run_agent` in `dashboard/main.py`**

Replace the entire `run_agent` function body (the `@app.post("/agents/{agent_id}/run")`
handler) with:

```python
@app.post("/agents/{agent_id}/run")
async def run_agent(agent_id: str, input: str = Form(...)) -> JSONResponse:
    if len(input) > MAX_RUN_INPUT_CHARS:
        return JSONResponse(
            {"error": f"input too large ({len(input)} chars); max is {MAX_RUN_INPUT_CHARS}"},
            status_code=413,
        )
    with Session(get_engine()) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        member = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.agent_id == agent_id)
        ).first()
        memory_block = workspace_memory.build_memory_context(
            s, member.workspace_id, input, k=WORKSPACE_MEMORY_K
        ) if member else ""
        result = run_agent_completion(s, agent, user_input=input, extra_system=memory_block)

    if result.blocked:
        msg = (f"⛔ Blocked by guardrails: {result.reason}" if result.block_stage == "input"
               else f"⛔ Output blocked: {result.reason}")
        return JSONResponse({
            "guardrail_verdict": "blocked",
            "reason": result.reason,
            "output": msg,
            "latency_ms": result.latency_ms,
        })

    return JSONResponse({
        "output": result.final_text,
        "guardrail_verdict": "passed",
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "stubbed": result.stubbed,
        "provider": result.provider,
        "model": result.model,
    })
```

Then DELETE the now-unused `_save_run` function (the `def _save_run(...)` block through
its final `record_event(...)` call) from `dashboard/main.py`.

Add the import near the other `core` imports in `dashboard/main.py`:
```python
from core.agent_runtime import run_agent_completion
```

Now remove imports that became unused. Run this to see what's still referenced:
`python - <<'PY'`
(or just grep). Specifically: after the refactor, `dashboard/main.py` no longer uses
`CompletionRequest`, `complete`, `Guardrails`, `record_event`, `AgentRun`, `time`, or
`uuid` *for run_agent*. **Before deleting any import, grep the whole file** for each name
(`Grep` for `CompletionRequest`, `complete`, `Guardrails`, `record_event`, `AgentRun`,
`time\.`, `uuid\.`) and only remove the import if it has zero remaining uses. Several of
these are used by OTHER routes (e.g. `record_event` may be used elsewhere, `uuid` and
`time` are widely used) — keep any that are still referenced. Leave imports that remain in
use untouched.

- [ ] **Step 5: Repoint the cycle-1 test fixture**

In `tests/test_workspaces.py`, the `client` fixture currently does:
```python
    monkeypatch.setattr(dash, "complete", fake_complete)
```
Replace that line with:
```python
    monkeypatch.setattr("core.llm_providers.complete", fake_complete)
```
(The `import dashboard.main as dash` line stays — `dash.app` is still used.)

- [ ] **Step 6: Run the targeted + full suite**

Run: `pytest tests/test_workspace_chat.py -k run_agent_completion -v`
Expected: PASS (2 tests)

Run: `pytest tests/ -v`
Expected: all PASS — especially `tests/test_workspaces.py::test_workspace_member_run_injects_memory`
and `test_non_workspace_agent_run_is_unchanged` (proves the refactor preserved behavior),
and `tests/test_agent_studio.py` run tests.

- [ ] **Step 7: Commit**

```bash
git add core/agent_runtime.py dashboard/main.py tests/test_workspaces.py tests/test_workspace_chat.py
git commit -m "refactor(agents): extract run_agent_completion shared by run endpoint and chat"
```

---

## Task 3: Mention resolution

**Files:**
- Create: `core/workspace_chat.py`
- Test: `tests/test_workspace_chat.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_chat.py`:

```python
from core import workspace_chat as wc


def test_resolve_mentions_basic():
    members = [("m1", "CEO", "ceo"), ("m2", "CFO", "cfo")]
    assert wc.resolve_mentions("hey @CEO what now", members) == ["m1"]
    assert wc.resolve_mentions("ping /cfo please", members) == ["m2"]


def test_resolve_mentions_longest_match_wins():
    members = [("m1", "Marketing", "marketing"),
               ("m2", "Marketing Lead", "marketing-lead")]
    # "@Marketing Lead" must resolve to m2, not m1
    assert wc.resolve_mentions("talk to @Marketing Lead now", members) == ["m2"]


def test_resolve_mentions_unknown_ignored_and_dedup():
    members = [("m1", "CEO", "ceo")]
    assert wc.resolve_mentions("@nobody here", members) == []
    assert wc.resolve_mentions("@CEO and again @ceo", members) == ["m1"]


def test_resolve_mentions_order_preserved():
    members = [("m1", "CEO", "ceo"), ("m2", "CFO", "cfo")]
    assert wc.resolve_mentions("@CFO then @CEO", members) == ["m2", "m1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_chat.py -k resolve_mentions -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.workspace_chat'`

- [ ] **Step 3: Create `core/workspace_chat.py` with `resolve_mentions`**

```python
"""Internal workspace chat: mention resolution + bounded agent-to-agent cascade.

A single channel per workspace. The owner or an agent posts a message; an agent
replies only when @mentioned (or addressed via /name). An agent reply that mentions
other agents triggers them, bounded by a turn budget and a per-cascade visited-set so
it can never loop. Each turn runs through agent_runtime.run_agent_completion grounded
in shared memory + recent transcript. No external services.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import deque
from typing import List, Optional, Tuple

from sqlmodel import Session, select

from core.agent_runtime import run_agent_completion
from core.state import (
    Agent, WorkspaceChatMessage, WorkspaceMember, utcnow,
)
from core.workspace_memory import build_memory_context

logger = logging.getLogger(__name__)

# Member descriptor used by mention resolution: (member_id, display_name, slug)
Member = Tuple[str, str, Optional[str]]


def resolve_mentions(text: str, members: List[Member]) -> List[str]:
    """Return member ids addressed by @name / /name tokens in `text`.

    Matches against each member's display name and agent slug, case-insensitive,
    longest-alias-first so multi-word names win over their prefixes. Overlapping
    matches are claimed once; results are de-duplicated and ordered by appearance.
    """
    aliases: List[Tuple[str, str]] = []
    for member_id, display_name, slug in members:
        if display_name:
            aliases.append((display_name.lower(), member_id))
        if slug:
            aliases.append((slug.lower(), member_id))
    aliases.sort(key=lambda a: len(a[0]), reverse=True)

    low = text.lower()
    claimed: List[Tuple[int, int]] = []
    hits: List[Tuple[int, str]] = []
    for alias, member_id in aliases:
        if not alias:
            continue
        pattern = re.compile(r"[@/]" + re.escape(alias) + r"(?![\w-])")
        for m in pattern.finditer(low):
            start, end = m.start(), m.end()
            if any(not (end <= cs or start >= ce) for cs, ce in claimed):
                continue
            claimed.append((start, end))
            hits.append((start, member_id))

    hits.sort(key=lambda h: h[0])
    ordered: List[str] = []
    for _, member_id in hits:
        if member_id not in ordered:
            ordered.append(member_id)
    return ordered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_chat.py -k resolve_mentions -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/workspace_chat.py tests/test_workspace_chat.py
git commit -m "feat(chat): add mention resolution (@name // name, longest-match)"
```

---

## Task 4: Cascade engine — post_message, _run_turn, bounded loop

**Files:**
- Modify: `core/workspace_chat.py` (append helpers + `post_message`)
- Test: `tests/test_workspace_chat.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_chat.py`:

```python
def _make_ws(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win the market", selected_roles=["ceo", "cfo"],
        )
        return ws.id


def test_owner_post_no_mention_creates_only_owner_message(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="just thinking")
        assert len(created) == 1
        assert created[0].author_kind == "owner"
        agent_msgs = s.exec(select(WorkspaceChatMessage).where(
            WorkspaceChatMessage.author_kind == "agent")).all()
        assert agent_msgs == []


def test_single_mention_triggers_one_reply_grounded(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="hey @CEO plan?")
        # owner message + exactly one CEO reply
        assert len(created) == 2
        reply = created[1]
        assert reply.author_kind == "agent"
        assert reply.triggered_by_id == created[0].id
        # grounded: the echoed system prompt carried mission + transcript
        assert "Win the market" in reply.content
        assert "Team chat (recent)" in reply.content


def test_cascade_is_bounded_and_loops_guarded(db, monkeypatch):
    # Every agent reply mentions "@CFO @CEO", which would loop forever unguarded.
    def chain_complete(req, provider):
        return CompletionResponse(text="@CFO @CEO keep going", provider=provider,
                                  model=req.model, stubbed=True)
    monkeypatch.setattr("core.llm_providers.complete", chain_complete)
    monkeypatch.setenv("WORKSPACE_CHAT_MAX_TURNS", "6")
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="@CEO start")
        agent_turns = [m for m in created if m.author_kind == "agent"]
        # Visited-set caps to one turn per member (2 members) regardless of budget
        assert len(agent_turns) <= 6
        triggered_members = [m.author_member_id for m in agent_turns]
        assert len(triggered_members) == len(set(triggered_members))  # no member twice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_chat.py -k "owner_post or single_mention or cascade" -v`
Expected: FAIL with `AttributeError: module 'core.workspace_chat' has no attribute 'post_message'`

- [ ] **Step 3: Append engine code to `core/workspace_chat.py`**

```python
def _load_members(session: Session, workspace_id: str) -> List[Tuple[str, str, Optional[str], str]]:
    """Return (member_id, display_name, slug, agent_id) for a workspace, ordered."""
    rows = session.exec(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.order_index)
    ).all()
    out = []
    for m in rows:
        agent = session.get(Agent, m.agent_id)
        slug = agent.slug if agent else None
        name = m.display_name or (agent.name if agent else m.role)
        out.append((m.id, name, slug, m.agent_id))
    return out


def _recent_transcript(session: Session, workspace_id: str, n: int) -> str:
    rows = session.exec(
        select(WorkspaceChatMessage).where(WorkspaceChatMessage.workspace_id == workspace_id)
        .order_by(WorkspaceChatMessage.created_at.desc()).limit(n)
    ).all()
    rows = list(reversed(rows))
    return "\n".join(f"{r.author_name}: {r.content}" for r in rows)


def _persist_message(session: Session, workspace_id: str, *, author_kind: str,
                     author_name: str, content: str, author_member_id: Optional[str],
                     mention_members: List[Member],
                     triggered_by_id: Optional[str]) -> WorkspaceChatMessage:
    mentions = resolve_mentions(content, mention_members)
    msg = WorkspaceChatMessage(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        author_kind=author_kind,
        author_member_id=author_member_id,
        author_name=author_name,
        content=content,
        mentions=json.dumps(mentions),
        triggered_by_id=triggered_by_id,
        created_at=utcnow(),
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _run_turn(session: Session, workspace_id: str,
              member: Tuple[str, str, Optional[str], str],
              trigger: WorkspaceChatMessage,
              mention_members: List[Member]) -> WorkspaceChatMessage:
    member_id, display_name, _slug, agent_id = member
    agent = session.get(Agent, agent_id)

    history_n = int(os.getenv("CHAT_HISTORY_N", "12"))
    memory_k = int(os.getenv("WORKSPACE_MEMORY_K", "5"))
    memory_block = build_memory_context(session, workspace_id, trigger.content, k=memory_k)
    transcript = _recent_transcript(session, workspace_id, history_n)

    parts = []
    if memory_block:
        parts.append(memory_block)
    parts.append("## Team chat (recent)\n" + transcript)
    extra_system = "\n\n".join(parts)

    result = run_agent_completion(session, agent, user_input=trigger.content,
                                  extra_system=extra_system)
    text = result.final_text if not result.blocked else f"⛔ (blocked: {result.reason})"

    return _persist_message(
        session, workspace_id, author_kind="agent", author_name=display_name,
        content=text, author_member_id=member_id, mention_members=mention_members,
        triggered_by_id=trigger.id,
    )


def post_message(session: Session, workspace_id: str, *, author_kind: str,
                 content: str, author_member_id: Optional[str] = None) -> List[WorkspaceChatMessage]:
    """Persist a message and run the bounded reply cascade. Returns all new messages
    (the originating message first, then any agent replies in cascade order)."""
    full_members = _load_members(session, workspace_id)
    mention_members: List[Member] = [(mid, name, slug) for (mid, name, slug, _aid) in full_members]
    member_by_id = {mid: (mid, name, slug, aid) for (mid, name, slug, aid) in full_members}

    author_name = "Owner"
    if author_kind == "agent" and author_member_id in member_by_id:
        author_name = member_by_id[author_member_id][1]

    origin = _persist_message(
        session, workspace_id, author_kind=author_kind, author_name=author_name,
        content=content, author_member_id=author_member_id,
        mention_members=mention_members, triggered_by_id=None,
    )
    created = [origin]

    max_turns = int(os.getenv("WORKSPACE_CHAT_MAX_TURNS", "6"))
    visited: set = set()
    queue: deque = deque((mid, origin) for mid in json.loads(origin.mentions or "[]"))
    turns = 0
    while queue and turns < max_turns:
        member_id, trigger = queue.popleft()
        if member_id in visited or member_id not in member_by_id:
            continue
        visited.add(member_id)
        reply = _run_turn(session, workspace_id, member_by_id[member_id], trigger, mention_members)
        created.append(reply)
        turns += 1
        for mid in json.loads(reply.mentions or "[]"):
            if mid not in visited:
                queue.append((mid, reply))
    if queue:
        logger.info("workspace_chat.cascade_budget_reached ws=%s dropped=%d",
                    workspace_id, len(queue))
    return created
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workspace_chat.py -k "owner_post or single_mention or cascade" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/workspace_chat.py tests/test_workspace_chat.py
git commit -m "feat(chat): add bounded agent-to-agent cascade engine"
```

---

## Task 5: Chat routes + template + detail link

**Files:**
- Modify: `dashboard/main.py` (add chat routes)
- Create: `dashboard/templates/workspace_chat.html`
- Modify: `dashboard/templates/workspace_detail.html` (add link)
- Test: `tests/test_workspace_chat.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_chat.py`:

```python
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    import dashboard.main as dash
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    return TestClient(dash.app)


def test_chat_view_and_post_flow(db, client):
    ws_id = _make_ws(db)
    # view renders
    r = client.get(f"/workspaces/{ws_id}/chat")
    assert r.status_code == 200

    # owner posts, mentioning CEO
    r = client.post(f"/workspaces/{ws_id}/chat", data={"content": "hey @CEO plan?"},
                    follow_redirects=False)
    assert r.status_code == 303

    # transcript now shows the owner line and a CEO reply
    detail = client.get(f"/workspaces/{ws_id}/chat")
    assert "hey @CEO plan?" in detail.text
    with Session(db) as s:
        agent_msgs = s.exec(select(WorkspaceChatMessage).where(
            WorkspaceChatMessage.author_kind == "agent")).all()
        assert len(agent_msgs) == 1


def test_chat_post_missing_workspace_404(db, client):
    r = client.post("/workspaces/ghost/chat", data={"content": "hi"},
                    follow_redirects=False)
    assert r.status_code == 404


def test_pin_message_to_memory(db, client):
    ws_id = _make_ws(db)
    with Session(db) as s:
        created = wc.post_message(s, ws_id, author_kind="owner", content="@CEO decide X")
        ceo_reply_id = created[1].id

    r = client.post(f"/workspaces/{ws_id}/chat/{ceo_reply_id}/pin",
                    data={"kind": "decision"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        msg = s.get(WorkspaceChatMessage, ceo_reply_id)
        assert msg.pinned_memory_id is not None
        mems = s.exec(select(WorkspaceMemory).where(
            WorkspaceMemory.id == msg.pinned_memory_id)).all()
        assert len(mems) == 1 and mems[0].kind == "decision"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_chat.py -k "chat_view or chat_post or pin_message" -v`
Expected: FAIL with 404s (routes don't exist)

- [ ] **Step 3: Add chat routes to `dashboard/main.py`**

Add the import for the chat engine near the other `core` imports:
```python
from core import workspace_chat
```

Add this block after the workspace archive route (`workspace_archive`) and before the
legacy project routes:

```python
@app.get("/workspaces/{workspace_id}/chat", response_class=HTMLResponse)
async def workspace_chat_view(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        members = s.exec(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.order_index)
        ).all()
        messages = s.exec(
            select(WorkspaceChatMessage).where(WorkspaceChatMessage.workspace_id == workspace_id)
            .order_by(WorkspaceChatMessage.created_at)
        ).all()
    return templates.TemplateResponse(request, "workspace_chat.html", {
        "ws": ws,
        "members": members,
        "messages": messages,
    })


@app.post("/workspaces/{workspace_id}/chat")
async def workspace_chat_post(workspace_id: str, content: str = Form(...)) -> RedirectResponse:
    text = content.strip()
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if text:
            workspace_chat.post_message(s, workspace_id, author_kind="owner", content=text)
    return RedirectResponse(url=f"/workspaces/{workspace_id}/chat", status_code=303)


@app.post("/workspaces/{workspace_id}/chat/{msg_id}/pin")
async def workspace_chat_pin(workspace_id: str, msg_id: str, request: Request) -> RedirectResponse:
    form = await request.form()
    kind = form.get("kind", "decision")
    kind = kind if kind in {"note", "fact", "decision", "goal"} else "decision"
    with Session(get_engine()) as s:
        msg = s.get(WorkspaceChatMessage, msg_id)
        if msg is None or msg.workspace_id != workspace_id:
            raise HTTPException(404, "message not found")
        entry = workspace_memory.add_entry(
            s, workspace_id, author=msg.author_name, kind=kind,
            content=msg.content, tags=["pinned"],
        )
        msg.pinned_memory_id = entry.id
        s.add(msg)
        s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/chat", status_code=303)
```

- [ ] **Step 4: Create `dashboard/templates/workspace_chat.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ ws.name }} · Team chat{% endblock %}
{% block content %}
<section class="max-w-4xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}" class="text-xs text-slate-500 hover:text-white">← {{ ws.name }}</a>
  <h1 class="text-2xl font-semibold text-white mt-1 mb-1">Team chat</h1>
  <p class="text-slate-400 text-sm mb-6">Address an agent with <code class="text-violet-300">@Name</code> or <code class="text-violet-300">/name</code>. Agents can mention each other — replies cascade, bounded so they can't loop.</p>

  <div class="space-y-3 mb-6">
    {% for m in messages %}
      <div class="flex {{ 'justify-end' if m.author_kind == 'owner' else 'justify-start' }} {{ 'pl-8' if m.triggered_by_id else '' }}">
        <div class="max-w-[80%] border border-white/10 rounded-xl p-3 {{ 'bg-violet-500/10' if m.author_kind == 'owner' else 'bg-white/5' }}">
          <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
            <span class="font-medium text-slate-300">{{ m.author_name }}</span>
            {% if m.pinned_memory_id %}<span class="text-amber-300">📌 pinned</span>{% endif %}
          </div>
          <p class="text-slate-100 text-sm whitespace-pre-wrap">{{ m.content }}</p>
          {% if m.author_kind == 'agent' and not m.pinned_memory_id %}
            <form method="post" action="/workspaces/{{ ws.id }}/chat/{{ m.id }}/pin" class="mt-2">
              <input type="hidden" name="kind" value="decision">
              <button class="text-xs text-slate-500 hover:text-amber-300 transition">📌 Pin to memory</button>
            </form>
          {% endif %}
        </div>
      </div>
    {% else %}
      <p class="text-slate-500 text-sm">No messages yet. Say hello to your team.</p>
    {% endfor %}
  </div>

  <form method="post" action="/workspaces/{{ ws.id }}/chat" class="space-y-2">
    <textarea name="content" rows="3" required placeholder="Message your team… e.g. @CEO what should we prioritize this quarter?" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white"></textarea>
    <button class="text-sm font-medium bg-grad-brand text-ink-950 px-5 py-2 rounded-md shadow-glow-v hover:shadow-glow-c transition">Send</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 5: Add the "Team chat" link in `dashboard/templates/workspace_detail.html`**

Find the back-link / header area near the top of the content section. Directly after the
`<a href="/workspaces" ...>← Workspaces</a>` line's containing `<div>` for the title
block, add a chat link. Concretely, locate the archive `<form>` block:
```html
    {% if ws.status == 'active' %}
      <form method="post" action="/workspaces/{{ ws.id }}/archive">
        <button class="text-xs text-slate-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition">Archive</button>
      </form>
    {% endif %}
```
and replace it with (adds a Team chat link beside Archive):
```html
    <div class="flex items-center gap-2">
      <a href="/workspaces/{{ ws.id }}/chat" class="text-xs text-violet-300 border border-violet-500/30 px-3 py-1.5 rounded-md hover:bg-violet-500/10 transition">Team chat →</a>
      {% if ws.status == 'active' %}
        <form method="post" action="/workspaces/{{ ws.id }}/archive">
          <button class="text-xs text-slate-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition">Archive</button>
        </form>
      {% endif %}
    </div>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_workspace_chat.py -k "chat_view or chat_post or pin_message" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add dashboard/main.py dashboard/templates/workspace_chat.html dashboard/templates/workspace_detail.html tests/test_workspace_chat.py
git commit -m "feat(chat): add chat view/post/pin routes, template, and detail link"
```

---

## Task 6: Config + docs

**Files:**
- Modify: `.env.example`, `CLAUDE.md`

- [ ] **Step 1: Document env vars in `.env.example`**

Append to `.env.example`:

```
# ── Internal workspace chat (subsystem D) ────────────────────
# Max agent turns per cascade (bounds agent-to-agent replies)
WORKSPACE_CHAT_MAX_TURNS=6
# Recent chat messages included as context for each agent turn
CHAT_HISTORY_N=12
```

- [ ] **Step 2: Document the layer in `CLAUDE.md`**

In `CLAUDE.md`, find item 6 "Workspace layer" (added last cycle). Append these bullets to
the end of item 6's sub-list (keep them indented as sibling bullets under item 6):

```markdown
   - `core/agent_runtime.py::run_agent_completion` — the shared guardrail→complete→persist core used by BOTH `/agents/{id}/run` and internal chat (single seam; tests patch `core.llm_providers.complete`).
   - `core/workspace_chat.py` — internal chat (subsystem D): one channel per workspace (`WorkspaceChatMessage`). `resolve_mentions` matches `@name`/`/name`; `post_message` runs a **bounded** agent-to-agent cascade (`WORKSPACE_CHAT_MAX_TURNS`, default 6) with a per-cascade visited-set so it cannot loop. Each turn reads shared memory + recent transcript (`CHAT_HISTORY_N`, default 12). Owner can pin a message into `WorkspaceMemory`. Routes: `/workspaces/{id}/chat` (+ post, `/chat/{msg}/pin`).
```

- [ ] **Step 3: Run the full suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "chore(chat): document chat env vars and the workspace_chat layer"
```

---

## Done — definition of complete

- `pytest tests/ -v` green, including `tests/test_workspace_chat.py`.
- `/workspaces/{id}/chat` renders; owner posting `@CEO …` produces a grounded CEO reply.
- An agent reply that mentions another agent triggers it, bounded by `WORKSPACE_CHAT_MAX_TURNS`, no member fires twice.
- Pinning a chat message creates a retrievable `WorkspaceMemory` entry.
- `/agents/{id}/run` behaves exactly as before the refactor (foundation tests still pass), proving chat and run share one guardrailed path.
