# Voice Marketing Agent (Subsystem F) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the workspace's Marketing member email outreach + follow-up capability (drafted via the shared guardrailed completion path) plus pluggable, fail-open STT/TTS and email providers.

**Architecture:** `core/voice.py` (STT/TTS) and `core/email_sender.py` (email) are pluggable, fail-open-to-stub modules like `embeddings`/`llm_providers`. `core/marketing.py` drafts outreach with `agent_runtime.run_agent_completion` on the Marketing member, sends via `email_sender`, and schedules follow-ups deterministically (`OutreachMessage.next_followup_at` + `followup_count`). Two new tables; marketing routes/UI under a workspace.

**Tech Stack:** Python 3, SQLModel/SQLite, FastAPI + Jinja2, pytest. Reuses `run_agent_completion`, `build_memory_context`.

**Spec:** `docs/superpowers/specs/2026-05-31-voice-marketing-design.md`

**Conventions:** `utcnow()` not `datetime.utcnow()`; fail-open providers; no `extra=` with a `message` key in logging; tests offline (`VOICE_BACKEND=stub`, `EMAIL_BACKEND=stub`, `EMBEDDING_BACKEND=stub`, completion monkeypatched). After each task: `pytest tests/ -v` green.

---

## File Structure

**New:** `core/voice.py`, `core/email_sender.py`, `core/marketing.py`, `dashboard/templates/workspace_marketing.html`, `tests/test_marketing.py`.
**Modified:** `core/state.py` (MarketingContact + OutreachMessage), `dashboard/main.py` (routes), `dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

---

## Task 1: Data model — MarketingContact + OutreachMessage

**Files:**
- Modify: `core/state.py`
- Test: `tests/test_marketing.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `tests/test_marketing.py`:

```python
"""Tests for the voice marketing agent (subsystem F)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, MarketingContact, OutreachMessage,
    get_engine, init_db, utcnow,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'mkt.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("VOICE_BACKEND", "stub")
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_marketing_tables_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(MarketingContact(id="ct1", workspace_id="w1", name="Lee",
                               email="lee@globex.com", company="Globex"))
        s.add(OutreachMessage(id="om1", workspace_id="w1", contact_id="ct1",
                              subject="Hi", body="Hello", send_status="sent"))
        s.commit()
    with Session(db) as s:
        ct = s.get(MarketingContact, "ct1")
        om = s.get(OutreachMessage, "om1")
        assert ct.status == "lead" and ct.email == "lee@globex.com"
        assert om.kind == "outreach" and om.followup_count == 0
        assert om.next_followup_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marketing.py::test_marketing_tables_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'MarketingContact' from 'core.state'`

- [ ] **Step 3: Edit `core/state.py`**

Add immediately AFTER `WorkspaceChannel` and BEFORE the `# Observability` banner:

```python
class MarketingContact(SQLModel, table=True):
    """A lead/client the marketing agent reaches out to (subsystem F)."""
    __tablename__ = "marketing_contact"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    email: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None
    status: str = "lead"                        # lead | contacted | replied | won | lost
    created_at: datetime = Field(default_factory=_utcnow)


class OutreachMessage(SQLModel, table=True):
    """One outreach or follow-up email drafted by the marketing agent (subsystem F)."""
    __tablename__ = "outreach_message"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    contact_id: str = Field(foreign_key="marketing_contact.id", index=True)
    member_id: Optional[str] = None             # Marketing WorkspaceMember.id
    kind: str = "outreach"                      # outreach | followup
    subject: str = ""
    body: str = ""
    channel: str = "email"
    send_status: str = "drafted"               # drafted | sent | failed
    followup_count: int = 0
    next_followup_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marketing.py::test_marketing_tables_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/test_marketing.py
git commit -m "feat(marketing): add MarketingContact + OutreachMessage tables"
```

---

## Task 2: Voice provider (STT/TTS), fail-open

**Files:**
- Create: `core/voice.py`
- Test: `tests/test_marketing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marketing.py`:

```python
from core import voice


def test_voice_stub_synthesize_and_transcribe(monkeypatch):
    monkeypatch.setenv("VOICE_BACKEND", "stub")
    audio = voice.synthesize("hello world")
    assert isinstance(audio, bytes) and len(audio) > 0
    assert voice.synthesize("hello world") == audio          # deterministic
    text = voice.transcribe(audio)
    assert isinstance(text, str) and text


def test_voice_unknown_backend_falls_back(monkeypatch):
    monkeypatch.setenv("VOICE_BACKEND", "nope")
    assert isinstance(voice.synthesize("x"), bytes)
    assert isinstance(voice.transcribe(b"abc"), str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marketing.py -k voice -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.voice'`

- [ ] **Step 3: Create `core/voice.py`**

```python
"""Pluggable speech providers (STT/TTS) for the marketing agent, fail-open to a stub.

VOICE_BACKEND selects the backend: "stub" (default; deterministic, no deps/network),
"openai" (Whisper STT + TTS), "elevenlabs" (TTS). Any real-backend failure falls back to
the stub with a logged warning, so missing keys/SDKs never break a flow or a test.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _stub_synthesize(text: str) -> bytes:
    # Deterministic, non-empty, derived from the text.
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return b"AUDIO:" + text.encode("utf-8") + b":" + digest[:8]


def _stub_transcribe(audio: bytes) -> str:
    return f"[transcript:{len(audio)} bytes]"


def synthesize(text: str, *, backend: Optional[str] = None) -> bytes:
    """Text → speech audio bytes (fail-open to stub)."""
    backend = backend or os.getenv("VOICE_BACKEND", "stub")
    if backend == "stub":
        return _stub_synthesize(text)
    try:
        if backend == "openai":
            from openai import OpenAI  # guarded
            client = OpenAI()
            resp = client.audio.speech.create(
                model=os.getenv("OPENAI_TTS_MODEL", "tts-1"),
                voice=os.getenv("OPENAI_TTS_VOICE", "alloy"), input=text,
            )
            return resp.read()
        if backend == "elevenlabs":
            import httpx
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
            key = os.getenv("ELEVENLABS_API_KEY", "")
            r = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": key}, json={"text": text}, timeout=30,
            )
            return r.content
        logger.warning("voice.unknown_backend=%s — using stub", backend)
        return _stub_synthesize(text)
    except Exception:
        logger.warning("voice.%s_synthesize_failed — stub", backend, exc_info=True)
        return _stub_synthesize(text)


def transcribe(audio: bytes, *, backend: Optional[str] = None) -> str:
    """Speech audio bytes → text (fail-open to stub)."""
    backend = backend or os.getenv("VOICE_BACKEND", "stub")
    if backend == "stub":
        return _stub_transcribe(audio)
    try:
        if backend == "openai":
            import io
            from openai import OpenAI  # guarded
            client = OpenAI()
            buf = io.BytesIO(audio)
            buf.name = "audio.wav"
            resp = client.audio.transcriptions.create(
                model=os.getenv("OPENAI_STT_MODEL", "whisper-1"), file=buf,
            )
            return resp.text
        logger.warning("voice.unknown_backend=%s — using stub", backend)
        return _stub_transcribe(audio)
    except Exception:
        logger.warning("voice.%s_transcribe_failed — stub", backend, exc_info=True)
        return _stub_transcribe(audio)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marketing.py -k voice -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/voice.py tests/test_marketing.py
git commit -m "feat(marketing): add pluggable STT/TTS voice provider (fail-open stub)"
```

---

## Task 3: Email sender, fail-open

**Files:**
- Create: `core/email_sender.py`
- Test: `tests/test_marketing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marketing.py`:

```python
from core import email_sender


def test_email_stub_returns_true_and_never_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    assert email_sender.send_email("a@b.com", "Hi", "Body") is True


def test_email_smtp_without_config_failopen(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    # No SMTP config: returns False, never raises.
    assert email_sender.send_email("a@b.com", "Hi", "Body") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marketing.py -k email -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.email_sender'`

- [ ] **Step 3: Create `core/email_sender.py`**

```python
"""Fail-open email sender for the marketing agent.

EMAIL_BACKEND selects the backend: "stub" (default; logs and returns True so outreach
flows complete offline) or "smtp" (uses SMTP_* env; any error → logs + False). Mirrors
core.notifier.SlackNotifier's fail-open philosophy — never raises.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str, *, backend: Optional[str] = None) -> bool:
    """Send an email. Fail-open: missing config / errors → log + False, never raises."""
    backend = backend or os.getenv("EMAIL_BACKEND", "stub")

    if backend == "stub":
        logger.info("email.stub_send to=%s subject=%s", to, subject)
        return True

    if backend == "smtp":
        host = os.getenv("SMTP_HOST", "")
        if not host:
            logger.warning("email.smtp_host_missing — skipping send")
            return False
        try:
            msg = EmailMessage()
            msg["From"] = os.getenv("SMTP_FROM", "noreply@example.com")
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                user = os.getenv("SMTP_USER", "")
                password = os.getenv("SMTP_PASSWORD", "")
                if user:
                    server.login(user, password)
                server.send_message(msg)
            return True
        except Exception:
            logger.warning("email.smtp_send_failed", exc_info=True)
            return False

    logger.warning("email.unknown_backend=%s — treating as stub", backend)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marketing.py -k email -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/email_sender.py tests/test_marketing.py
git commit -m "feat(marketing): add fail-open email sender (stub/smtp)"
```

---

## Task 4: Marketing engine — draft, send, follow-up cadence

**Files:**
- Create: `core/marketing.py`
- Test: `tests/test_marketing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marketing.py`:

```python
from datetime import timedelta

from core import marketing
from core import workspace_factory as wf
from core.llm_providers import CompletionResponse


def _echo_complete(req, provider):
    return CompletionResponse(text=f"BODY[{req.system[:12]}]", provider=provider,
                              model=req.model, stubbed=True)


def _ws_with_marketing(db):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["marketing"],
        )
        member = s.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id)).one()
        contact = MarketingContact(id="ct1", workspace_id=ws.id, name="Lee",
                                   email="lee@globex.com", company="Globex")
        s.add(contact)
        s.commit()
        return ws.id, member.id, "ct1"


def test_send_outreach_persists_and_schedules(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        member = s.get(WorkspaceMember, member_id)
        contact = s.get(MarketingContact, ct_id)
        msg = marketing.send_outreach(s, ws_id, member, contact, "demo our product",
                                      followup_days=3)
        assert msg.send_status == "sent"
        assert msg.kind == "outreach"
        assert msg.next_followup_at is not None
        assert msg.body.startswith("BODY[")
        assert s.get(MarketingContact, ct_id).status == "contacted"


def test_followup_cadence_bounded(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    monkeypatch.setenv("MARKETING_MAX_FOLLOWUPS", "2")
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        member = s.get(WorkspaceMember, member_id)
        contact = s.get(MarketingContact, ct_id)
        outreach = marketing.send_outreach(s, ws_id, member, contact, "demo", followup_days=3)
        # force it due
        outreach.next_followup_at = utcnow() - timedelta(days=1)
        s.add(outreach); s.commit()

        due = marketing.due_followups(s, ws_id)
        assert len(due) == 1

        f1 = marketing.send_followup(s, member, due[0], followup_days=0)
        assert f1.kind == "followup" and f1.send_status == "sent"
        s.refresh(outreach)
        assert outreach.followup_count == 1

        # second follow-up reaches the cap → original is closed (next_followup_at None)
        due2 = marketing.due_followups(s, ws_id)
        marketing.send_followup(s, member, due2[0], followup_days=0)
        s.refresh(outreach)
        assert outreach.followup_count == 2
        assert marketing.due_followups(s, ws_id) == []   # capped out


def test_find_marketing_member(db):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    with Session(db) as s:
        m = marketing.find_marketing_member(s, ws_id)
        assert m is not None and m.role == "marketing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marketing.py -k "outreach or followup or marketing_member" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.marketing'`

- [ ] **Step 3: Create `core/marketing.py`**

```python
"""Marketing engine: agent-drafted outreach emails + bounded follow-up cadence.

Drafts go through the shared agent_runtime.run_agent_completion on the workspace's
Marketing member (guardrailed, grounded in shared memory, observable). Sending uses the
fail-open email_sender. Follow-ups are deterministic and bounded by MARKETING_MAX_FOLLOWUPS.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlmodel import Session, select

from core import email_sender
from core.agent_runtime import run_agent_completion
from core.state import (
    Agent, MarketingContact, OutreachMessage, WorkspaceMember, utcnow,
)
from core.workspace_memory import build_memory_context

logger = logging.getLogger(__name__)


def find_marketing_member(session: Session, workspace_id: str) -> Optional[WorkspaceMember]:
    """Return the workspace's Marketing member (role 'marketing'), or None."""
    return session.exec(
        select(WorkspaceMember).where(
            (WorkspaceMember.workspace_id == workspace_id)
            & (WorkspaceMember.role == "marketing")
        ).order_by(WorkspaceMember.order_index)
    ).first()


def _subject_for(goal: str, kind: str) -> str:
    base = (goal or "Hello").strip().splitlines()[0][:60] or "Hello"
    return ("Following up: " + base) if kind == "followup" else base


def draft_outreach(session: Session, member: WorkspaceMember, contact: MarketingContact,
                   goal: str, *, kind: str = "outreach",
                   prior_body: Optional[str] = None) -> Tuple[str, str]:
    """Draft (subject, body) for an outreach/follow-up email via the Marketing agent."""
    agent = session.get(Agent, member.agent_id)
    memory_block = build_memory_context(
        session, member.workspace_id, goal, k=int(os.getenv("WORKSPACE_MEMORY_K", "5"))
    )
    instr = f"Write a concise, friendly {'follow-up ' if kind == 'followup' else ''}outreach email to {contact.name}"
    if contact.company:
        instr += f" at {contact.company}"
    instr += f" about: {goal}."
    if prior_body:
        instr += f"\n\nPrior email you sent:\n{prior_body}\n\nWrite a brief, value-adding follow-up."
    instr += "\nReturn just the email body (no subject line)."

    result = run_agent_completion(session, agent, user_input=instr, extra_system=memory_block)
    body = result.final_text if not result.blocked else "(message blocked by guardrails)"
    return _subject_for(goal, kind), body


def send_outreach(session: Session, workspace_id: str, member: WorkspaceMember,
                  contact: MarketingContact, goal: str, *,
                  followup_days: Optional[int] = None) -> OutreachMessage:
    """Draft + send an initial outreach email; schedule the first follow-up."""
    if followup_days is None:
        followup_days = int(os.getenv("MARKETING_FOLLOWUP_DAYS", "3"))
    subject, body = draft_outreach(session, member, contact, goal, kind="outreach")
    ok = email_sender.send_email(contact.email or "", subject, body)

    msg = OutreachMessage(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        contact_id=contact.id,
        member_id=member.id,
        kind="outreach",
        subject=subject,
        body=body,
        channel="email",
        send_status="sent" if ok else "failed",
        followup_count=0,
        next_followup_at=utcnow() + timedelta(days=followup_days),
        created_at=utcnow(),
    )
    session.add(msg)
    contact.status = "contacted"
    session.add(contact)
    session.commit()
    session.refresh(msg)
    return msg


def due_followups(session: Session, workspace_id: str, now=None) -> List[OutreachMessage]:
    """Sent outreach whose follow-up is due and under the cap."""
    now = now or utcnow()
    max_f = int(os.getenv("MARKETING_MAX_FOLLOWUPS", "2"))
    stmt = select(OutreachMessage).where(
        (OutreachMessage.workspace_id == workspace_id)
        & (OutreachMessage.kind == "outreach")
        & (OutreachMessage.send_status == "sent")
        & (OutreachMessage.next_followup_at != None)  # noqa: E711
        & (OutreachMessage.next_followup_at <= now)
        & (OutreachMessage.followup_count < max_f)
    )
    return list(session.exec(stmt).all())


def send_followup(session: Session, member: WorkspaceMember, outreach: OutreachMessage, *,
                  followup_days: Optional[int] = None) -> OutreachMessage:
    """Draft + send one follow-up for `outreach`; advance the cadence (bounded)."""
    if followup_days is None:
        followup_days = int(os.getenv("MARKETING_FOLLOWUP_DAYS", "3"))
    max_f = int(os.getenv("MARKETING_MAX_FOLLOWUPS", "2"))
    contact = session.get(MarketingContact, outreach.contact_id)
    subject, body = draft_outreach(session, member, contact, outreach.subject,
                                   kind="followup", prior_body=outreach.body)
    ok = email_sender.send_email(contact.email or "" if contact else "", subject, body)

    fmsg = OutreachMessage(
        id=str(uuid.uuid4()),
        workspace_id=outreach.workspace_id,
        contact_id=outreach.contact_id,
        member_id=member.id,
        kind="followup",
        subject=subject,
        body=body,
        channel="email",
        send_status="sent" if ok else "failed",
        followup_count=0,
        next_followup_at=None,
        created_at=utcnow(),
    )
    session.add(fmsg)

    outreach.followup_count += 1
    if outreach.followup_count >= max_f:
        outreach.next_followup_at = None          # cadence complete
    else:
        outreach.next_followup_at = utcnow() + timedelta(days=followup_days)
    session.add(outreach)
    session.commit()
    session.refresh(fmsg)
    return fmsg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_marketing.py -k "outreach or followup or marketing_member" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/marketing.py tests/test_marketing.py
git commit -m "feat(marketing): add outreach drafting, sending, and bounded follow-up cadence"
```

---

## Task 5: Marketing routes + UI + voice demo

**Files:**
- Modify: `dashboard/main.py`
- Create: `dashboard/templates/workspace_marketing.html`
- Modify: `dashboard/templates/workspace_detail.html`
- Test: `tests/test_marketing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_marketing.py`:

```python
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    import dashboard.main as dash
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    return TestClient(dash.app)


def test_marketing_page_and_contact_and_outreach(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    r = client.get(f"/workspaces/{ws_id}/marketing")
    assert r.status_code == 200

    # add a contact
    r = client.post(f"/workspaces/{ws_id}/marketing/contacts", data={
        "name": "Dana", "email": "dana@x.com", "company": "X"}, follow_redirects=False)
    assert r.status_code == 303

    # launch outreach to the seeded contact
    r = client.post(f"/workspaces/{ws_id}/marketing/outreach", data={
        "contact_id": ct_id, "goal": "demo our product"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        msgs = s.exec(select(OutreachMessage).where(
            OutreachMessage.workspace_id == ws_id)).all()
        assert len(msgs) == 1 and msgs[0].send_status == "sent"


def test_run_followups_route(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    client.post(f"/workspaces/{ws_id}/marketing/outreach", data={
        "contact_id": ct_id, "goal": "demo"}, follow_redirects=False)
    with Session(db) as s:
        om = s.exec(select(OutreachMessage).where(
            OutreachMessage.workspace_id == ws_id)).one()
        om.next_followup_at = utcnow() - timedelta(days=1)
        s.add(om); s.commit()
    r = client.post(f"/workspaces/{ws_id}/marketing/followups/run", follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        followups = s.exec(select(OutreachMessage).where(
            OutreachMessage.kind == "followup")).all()
        assert len(followups) == 1


def test_voice_preview_returns_audio(db, client):
    ws_id, member_id, ct_id = _ws_with_marketing(db)
    r = client.post(f"/workspaces/{ws_id}/marketing/voice/preview",
                    data={"text": "Hello there"})
    assert r.status_code == 200
    assert len(r.content) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_marketing.py -k "marketing_page or followups_route or voice_preview" -v`
Expected: FAIL with 404s (routes don't exist)

- [ ] **Step 3: Add imports + routes to `dashboard/main.py`**

Add near the other `core` imports:
```python
from core import marketing, voice
```
Add `MarketingContact, OutreachMessage` to the `from core.state import (...)` block.

Add this block after the bridge/channel routes and before the legacy project routes:

```python
@app.get("/workspaces/{workspace_id}/marketing", response_class=HTMLResponse)
async def workspace_marketing(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        contacts = s.exec(
            select(MarketingContact).where(MarketingContact.workspace_id == workspace_id)
            .order_by(MarketingContact.created_at.desc())
        ).all()
        messages = s.exec(
            select(OutreachMessage).where(OutreachMessage.workspace_id == workspace_id)
            .order_by(OutreachMessage.created_at.desc()).limit(50)
        ).all()
        has_marketing = marketing.find_marketing_member(s, workspace_id) is not None
    return templates.TemplateResponse(request, "workspace_marketing.html", {
        "ws": ws,
        "contacts": contacts,
        "messages": messages,
        "has_marketing": has_marketing,
    })


@app.post("/workspaces/{workspace_id}/marketing/contacts")
async def workspace_marketing_add_contact(workspace_id: str, name: str = Form(...),
                                          email: str = Form(""),
                                          company: str = Form(""),
                                          notes: str = Form("")) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if name.strip():
            s.add(MarketingContact(
                id=str(uuid.uuid4()), workspace_id=workspace_id, name=name.strip(),
                email=email.strip() or None, company=company.strip() or None,
                notes=notes.strip() or None,
            ))
            s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/outreach")
async def workspace_marketing_outreach(workspace_id: str, contact_id: str = Form(...),
                                       goal: str = Form(...)) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        member = marketing.find_marketing_member(s, workspace_id)
        contact = s.get(MarketingContact, contact_id)
        if member is not None and contact is not None and contact.workspace_id == workspace_id:
            marketing.send_outreach(s, workspace_id, member, contact, goal.strip())
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/followups/run")
async def workspace_marketing_run_followups(workspace_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        member = marketing.find_marketing_member(s, workspace_id)
        if member is not None:
            for outreach in marketing.due_followups(s, workspace_id):
                marketing.send_followup(s, member, outreach)
    return RedirectResponse(url=f"/workspaces/{workspace_id}/marketing", status_code=303)


@app.post("/workspaces/{workspace_id}/marketing/voice/preview")
async def workspace_marketing_voice_preview(workspace_id: str,
                                            text: str = Form(...)) -> Response:
    audio = voice.synthesize(text.strip() or "Hello")
    return Response(content=audio, media_type="application/octet-stream")
```

Note: add `Response` to the FastAPI imports if not present. The existing import line is
`from fastapi import FastAPI, Form, HTTPException, Request` — change it to
`from fastapi import FastAPI, Form, HTTPException, Request, Response`.

- [ ] **Step 4: Create `dashboard/templates/workspace_marketing.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ ws.name }} · Marketing{% endblock %}
{% block content %}
<section class="max-w-4xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}" class="text-xs text-slate-500 hover:text-white">← {{ ws.name }}</a>
  <h1 class="text-2xl font-semibold text-white mt-1 mb-1">Marketing</h1>
  {% if not has_marketing %}
    <p class="text-amber-300 text-sm mb-6">No Marketing agent in this workspace — add the Marketing role to draft and send outreach.</p>
  {% else %}
    <p class="text-slate-400 text-sm mb-6">Your Marketing agent drafts outreach grounded in shared memory, sends it, and follows up automatically.</p>
  {% endif %}

  <div class="grid lg:grid-cols-2 gap-8">
    <div>
      <h2 class="text-sm uppercase tracking-wide text-slate-400 mb-3">Contacts</h2>
      <form method="post" action="/workspaces/{{ ws.id }}/marketing/contacts" class="space-y-2 mb-4">
        <input name="name" required placeholder="Name" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white">
        <input name="email" placeholder="Email" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white">
        <input name="company" placeholder="Company" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white">
        <button class="text-sm font-medium bg-grad-brand text-ink-950 px-4 py-1.5 rounded-md">Add contact</button>
      </form>
      <div class="space-y-2">
        {% for c in contacts %}
          <div class="border border-white/10 rounded-lg p-3">
            <div class="flex items-center justify-between">
              <div class="text-white text-sm font-medium">{{ c.name }} <span class="text-slate-500 text-xs">{{ c.company or '' }}</span></div>
              <span class="text-xs px-2 py-0.5 rounded-full bg-white/5 text-slate-300">{{ c.status }}</span>
            </div>
            <div class="text-slate-500 text-xs">{{ c.email or 'no email' }}</div>
            {% if has_marketing and c.email %}
            <form method="post" action="/workspaces/{{ ws.id }}/marketing/outreach" class="mt-2 flex gap-2">
              <input type="hidden" name="contact_id" value="{{ c.id }}">
              <input name="goal" required placeholder="Outreach goal…" class="flex-1 bg-ink-900 border border-white/10 rounded px-2 py-1 text-xs text-white">
              <button class="text-xs bg-grad-brand text-ink-950 px-3 rounded">Reach out</button>
            </form>
            {% endif %}
          </div>
        {% else %}
          <p class="text-slate-500 text-sm">No contacts yet.</p>
        {% endfor %}
      </div>
    </div>

    <div>
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-sm uppercase tracking-wide text-slate-400">Outreach log</h2>
        <form method="post" action="/workspaces/{{ ws.id }}/marketing/followups/run">
          <button class="text-xs text-violet-300 border border-violet-500/30 px-3 py-1 rounded-md hover:bg-violet-500/10 transition">Run due follow-ups</button>
        </form>
      </div>
      <div class="space-y-2 max-h-[28rem] overflow-y-auto scrollbar-thin">
        {% for m in messages %}
          <div class="border border-white/10 rounded-lg p-3">
            <div class="flex items-center gap-2 text-xs text-slate-500 mb-1">
              <span class="px-1.5 py-0.5 rounded bg-white/5 text-slate-300">{{ m.kind }}</span>
              <span>{{ m.send_status }}</span>
            </div>
            <div class="text-white text-sm font-medium">{{ m.subject }}</div>
            <p class="text-slate-300 text-xs mt-1 whitespace-pre-wrap">{{ m.body[:240] }}</p>
          </div>
        {% else %}
          <p class="text-slate-500 text-sm">No outreach yet.</p>
        {% endfor %}
      </div>

      <h2 class="text-sm uppercase tracking-wide text-slate-400 mt-6 mb-2">Voice preview (TTS)</h2>
      <form method="post" action="/workspaces/{{ ws.id }}/marketing/voice/preview" class="flex gap-2">
        <input name="text" required placeholder="Type a line to synthesize…" class="flex-1 bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-sm text-white">
        <button class="text-sm bg-grad-brand text-ink-950 px-4 rounded-md">Synthesize</button>
      </form>
    </div>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 5: Add a "Marketing" link in `dashboard/templates/workspace_detail.html`**

In the `<div class="flex items-center gap-2">` action row, add (after "Channels →", before "Team chat →"):
```html
      <a href="/workspaces/{{ ws.id }}/marketing" class="text-xs text-pink-300 border border-pink-500/30 px-3 py-1.5 rounded-md hover:bg-pink-500/10 transition">Marketing →</a>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_marketing.py -k "marketing_page or followups_route or voice_preview" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add dashboard/main.py dashboard/templates/workspace_marketing.html dashboard/templates/workspace_detail.html tests/test_marketing.py
git commit -m "feat(marketing): add marketing routes, outreach/follow-up UI, voice preview"
```

---

## Task 6: Config + docs

**Files:**
- Modify: `.env.example`, `CLAUDE.md`

- [ ] **Step 1: Document env vars in `.env.example`**

Append to `.env.example`:

```
# ── Voice marketing agent (subsystem F) ──────────────────────
# STT/TTS backend: stub | openai | elevenlabs  (absent → stub, fail-open)
VOICE_BACKEND=stub
# Email backend: stub | smtp  (stub logs + succeeds; smtp uses SMTP_* below)
EMAIL_BACKEND=stub
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=noreply@example.com
# Follow-up cadence
MARKETING_MAX_FOLLOWUPS=2
MARKETING_FOLLOWUP_DAYS=3
```

- [ ] **Step 2: Document the layer in `CLAUDE.md`**

Append these bullets to the END of item 6 ("Workspace layer") sub-list (3-space indent + `- `):

```markdown
   - `core/voice.py` — pluggable STT/TTS (`VOICE_BACKEND`: `stub`|`openai`|`elevenlabs`), fail-open to a deterministic stub. `synthesize(text)->bytes`, `transcribe(audio)->str`.
   - `core/email_sender.py` — fail-open `send_email` (`EMAIL_BACKEND`: `stub`|`smtp`), mirrors `notifier.SlackNotifier` (never raises).
   - `core/marketing.py` — the Marketing agent (subsystem F): `draft_outreach` writes emails via the shared `run_agent_completion` on the workspace's Marketing member (grounded in shared memory); `send_outreach` sends + schedules; `due_followups`/`send_followup` run a **bounded** follow-up cadence (`MARKETING_MAX_FOLLOWUPS`, default 2). Tables `MarketingContact`/`OutreachMessage`. Routes: `/workspaces/{id}/marketing` (contacts, outreach, run-followups, TTS voice preview). Real telephony is deferred; STT/TTS providers + a demo are in.
```

- [ ] **Step 3: Run the full suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "chore(marketing): document voice/email/marketing env vars and the layer"
```

---

## Done — definition of complete

- `pytest tests/ -v` green, including `tests/test_marketing.py`.
- A workspace with a Marketing member can add contacts, launch agent-drafted outreach (sent via fail-open email), and run a bounded follow-up cadence.
- Voice STT/TTS providers are pluggable and fail-open; the marketing page's TTS preview returns audio bytes.
- All providers run offline with no credentials; nothing raises.
