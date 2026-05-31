# External Chat Bridge (Subsystem E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route inbound Telegram/Slack/WhatsApp messages that address an agent (`/<name>`) through the existing internal chat engine and send the agent's replies back to the originating platform.

**Architecture:** A `WorkspaceChannel` maps a workspace to a `(platform, external_id)`. `core/chat_bridge.py` parses inbound webhook payloads, routes them via the existing `workspace_chat.post_message` (reusing mention resolution + bounded cascade), and sends replies back with a fail-open `send_message` (mirrors `SlackNotifier`). Webhook routes always return 200.

**Tech Stack:** Python 3, SQLModel/SQLite, FastAPI + Jinja2, httpx (already a dep), pytest. Reuses subsystem D (`workspace_chat`).

**Spec:** `docs/superpowers/specs/2026-05-31-external-chat-bridge-design.md`

**Conventions:** `utcnow()` not `datetime.utcnow()`; fail-open outbound; no `extra=` with a `message` key in logging; tests offline (`EMBEDDING_BACKEND=stub`, `send_message` + completion monkeypatched). After each task: `pytest tests/ -v` green.

---

## File Structure

**New:** `core/chat_bridge.py`, `dashboard/templates/workspace_channels.html`, `tests/test_chat_bridge.py`.
**Modified:** `core/state.py` (WorkspaceChannel), `dashboard/main.py` (webhook + channel routes), `dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

---

## Task 1: WorkspaceChannel table

**Files:**
- Modify: `core/state.py`
- Test: `tests/test_chat_bridge.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_bridge.py`:

```python
"""Tests for the external chat bridge (subsystem E)."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from core.state import (
    Agent, Workspace, WorkspaceMember, WorkspaceChannel,
    get_engine, init_db,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'bridge.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return get_engine()


def test_channel_roundtrip(db):
    with Session(db) as s:
        s.add(Workspace(id="w1", owner_email="o@x.com", name="Acme"))
        s.add(WorkspaceChannel(
            id="c1", workspace_id="w1", platform="telegram",
            external_id="12345", label="Founders", token_env="TELEGRAM_BOT_TOKEN",
        ))
        s.commit()
    with Session(db) as s:
        ch = s.exec(select(WorkspaceChannel).where(
            WorkspaceChannel.platform == "telegram")).one()
        assert ch.external_id == "12345"
        assert ch.active is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_bridge.py::test_channel_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'WorkspaceChannel' from 'core.state'`

- [ ] **Step 3: Edit `core/state.py`**

Add immediately AFTER `WorkspaceProject` and BEFORE the `# Observability` banner:

```python
class WorkspaceChannel(SQLModel, table=True):
    """Links a workspace to an external chat platform channel (subsystem E)."""
    __tablename__ = "workspace_channel"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    platform: str                               # telegram | slack | whatsapp
    external_id: str = Field(index=True)        # chat / channel / phone id
    label: Optional[str] = None
    token_env: Optional[str] = None             # env var name holding the bot token
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chat_bridge.py::test_channel_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/state.py tests/test_chat_bridge.py
git commit -m "feat(bridge): add WorkspaceChannel table"
```

---

## Task 2: Bridge module — parse_inbound + send_message

**Files:**
- Create: `core/chat_bridge.py`
- Test: `tests/test_chat_bridge.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_bridge.py`:

```python
from core import chat_bridge as cb


def test_parse_inbound_telegram():
    msg = cb.parse_inbound("telegram", {"message": {"chat": {"id": 42}, "text": "/ceo hi"}})
    assert msg is not None
    assert msg.external_id == "42" and msg.text == "/ceo hi"


def test_parse_inbound_slack_message_and_challenge():
    msg = cb.parse_inbound("slack", {"event": {"channel": "C9", "text": "hey @CEO"}})
    assert msg.external_id == "C9" and msg.text == "hey @CEO"
    chal = cb.parse_inbound("slack", {"type": "url_verification", "challenge": "abc"})
    assert chal.challenge == "abc"
    # bot's own message ignored
    assert cb.parse_inbound("slack", {"event": {"channel": "C9", "text": "x", "bot_id": "B1"}}) is None


def test_parse_inbound_whatsapp_and_junk():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "555", "text": {"body": "hello"}}]}}]}]}
    msg = cb.parse_inbound("whatsapp", payload)
    assert msg.external_id == "555" and msg.text == "hello"
    assert cb.parse_inbound("telegram", {"nothing": True}) is None


def test_send_message_failopen_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # No token, no network: returns False, never raises.
    assert cb.send_message("telegram", "42", "hi", token=None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_bridge.py -k "parse_inbound or send_message" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.chat_bridge'`

- [ ] **Step 3: Create `core/chat_bridge.py`**

```python
"""External chat bridge: Telegram / Slack / WhatsApp ↔ internal workspace chat.

Inbound webhook payloads are normalized (parse_inbound), routed through the existing
workspace_chat.post_message (so /<name> addressing, mention resolution, and the bounded
agent-to-agent cascade are reused), and each agent reply is sent back to the platform via
a fail-open send_message (mirrors core.notifier.SlackNotifier — no token → no-op, never
raises). Nothing here requires credentials to run or test.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

import httpx

from sqlmodel import Session, select

from core.state import WorkspaceChannel
from core import workspace_chat

logger = logging.getLogger(__name__)


BRIDGE_PLATFORMS: Dict[str, Dict] = {
    "telegram": {"label": "Telegram", "token_env": "TELEGRAM_BOT_TOKEN"},
    "slack":    {"label": "Slack",    "token_env": "SLACK_BOT_TOKEN"},
    "whatsapp": {"label": "WhatsApp", "token_env": "WHATSAPP_TOKEN"},
}


@dataclass
class InboundMessage:
    external_id: str
    text: str
    sender: Optional[str] = None
    challenge: Optional[str] = None


def parse_inbound(platform: str, payload: Dict) -> Optional[InboundMessage]:
    """Normalize a platform webhook payload. Returns None when there's no message."""
    try:
        if platform == "telegram":
            m = payload.get("message") or {}
            chat = m.get("chat") or {}
            text = m.get("text")
            if chat.get("id") is None or text is None:
                return None
            return InboundMessage(external_id=str(chat["id"]), text=text,
                                  sender=str((m.get("from") or {}).get("id", "")) or None)

        if platform == "slack":
            if payload.get("type") == "url_verification":
                return InboundMessage(external_id="", text="",
                                      challenge=payload.get("challenge"))
            ev = payload.get("event") or {}
            # ignore bot/self messages and non-message events
            if ev.get("bot_id") or ev.get("type") not in (None, "message", "app_mention"):
                return None
            if ev.get("channel") is None or ev.get("text") is None:
                return None
            return InboundMessage(external_id=str(ev["channel"]), text=ev["text"],
                                  sender=ev.get("user"))

        if platform == "whatsapp":
            value = (((payload.get("entry") or [{}])[0].get("changes") or [{}])[0]
                     .get("value") or {})
            messages = value.get("messages") or []
            if not messages:
                return None
            first = messages[0]
            body = (first.get("text") or {}).get("body")
            sender = first.get("from")
            if body is None or sender is None:
                return None
            return InboundMessage(external_id=str(sender), text=body, sender=str(sender))
    except Exception:
        logger.warning("chat_bridge.parse_failed platform=%s", platform, exc_info=True)
        return None
    return None


def send_message(platform: str, channel: str, text: str, *,
                 token: Optional[str] = None) -> bool:
    """Send `text` to `channel` on `platform`. Fail-open: no token / network error → False."""
    meta = BRIDGE_PLATFORMS.get(platform)
    if meta is None:
        logger.warning("chat_bridge.unknown_platform=%s", platform)
        return False
    token = token or os.getenv(meta["token_env"], "")
    if not token:
        logger.warning("chat_bridge.token_missing platform=%s — skipping send", platform)
        return False
    try:
        if platform == "telegram":
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            r = httpx.post(url, json={"chat_id": channel, "text": text}, timeout=10)
        elif platform == "slack":
            r = httpx.post("https://slack.com/api/chat.postMessage",
                           headers={"Authorization": f"Bearer {token}"},
                           json={"channel": channel, "text": text}, timeout=10)
        elif platform == "whatsapp":
            phone_id = os.getenv("WHATSAPP_PHONE_ID", "")
            url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
            r = httpx.post(url, headers={"Authorization": f"Bearer {token}"},
                           json={"messaging_product": "whatsapp", "to": channel,
                                 "text": {"body": text}}, timeout=10)
        else:
            return False
        return r.status_code < 300
    except Exception:
        logger.warning("chat_bridge.send_failed platform=%s", platform, exc_info=True)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chat_bridge.py -k "parse_inbound or send_message" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/chat_bridge.py tests/test_chat_bridge.py
git commit -m "feat(bridge): add platform registry, parse_inbound, fail-open send_message"
```

---

## Task 3: handle_inbound routing

**Files:**
- Modify: `core/chat_bridge.py` (append `handle_inbound`)
- Test: `tests/test_chat_bridge.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_bridge.py`:

```python
from core import workspace_factory as wf
from core.llm_providers import CompletionResponse


def _echo_complete(req, provider):
    return CompletionResponse(text=f"REPLY::{req.system[:20]}", provider=provider,
                              model=req.model, stubbed=True)


def test_handle_inbound_routes_to_chat_and_replies(db, monkeypatch):
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    sent = []
    monkeypatch.setattr(cb, "send_message",
                        lambda platform, channel, text, **kw: sent.append((channel, text)) or True)
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        s.add(WorkspaceChannel(id="c1", workspace_id=ws.id, platform="telegram",
                               external_id="42", token_env="TELEGRAM_BOT_TOKEN"))
        s.commit()

        result = cb.handle_inbound(s, "telegram",
                                   {"message": {"chat": {"id": 42}, "text": "/ceo plan?"}})
        assert result["handled"] is True
        assert result["replies"] >= 1
        # a reply was sent back to the same external id
        assert sent and sent[0][0] == "42"
        msgs = s.exec(select(__import__("core.state", fromlist=["WorkspaceChatMessage"]).WorkspaceChatMessage)).all()
        assert any(m.author_kind == "agent" for m in msgs)


def test_handle_inbound_unmapped_channel(db):
    with Session(db) as s:
        s.add(Workspace(id="w2", owner_email="o@x.com", name="Acme2"))
        s.commit()
        result = cb.handle_inbound(s, "telegram",
                                   {"message": {"chat": {"id": 999}, "text": "hi"}})
        assert result["handled"] is False


def test_handle_inbound_slack_challenge(db):
    with Session(db) as s:
        result = cb.handle_inbound(s, "slack", {"type": "url_verification", "challenge": "z"})
        assert result.get("challenge") == "z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_bridge.py -k handle_inbound -v`
Expected: FAIL with `AttributeError: module 'core.chat_bridge' has no attribute 'handle_inbound'`

- [ ] **Step 3: Append `handle_inbound` to `core/chat_bridge.py`**

```python
def handle_inbound(session: Session, platform: str, payload: Dict) -> Dict:
    """Route an inbound platform message into workspace chat and reply back.

    Always safe to call: unparseable/unmapped inbound returns {"handled": False}.
    """
    msg = parse_inbound(platform, payload)
    if msg is None:
        return {"handled": False, "reason": "unparseable"}
    if msg.challenge is not None:
        return {"challenge": msg.challenge}

    channel = session.exec(
        select(WorkspaceChannel).where(
            (WorkspaceChannel.platform == platform)
            & (WorkspaceChannel.external_id == msg.external_id)
            & (WorkspaceChannel.active == True)  # noqa: E712
        )
    ).first()
    if channel is None:
        return {"handled": False, "reason": "unmapped"}

    created = workspace_chat.post_message(
        session, channel.workspace_id, author_kind="owner", content=msg.text
    )
    token = os.getenv(channel.token_env, "") if channel.token_env else ""
    replies = [m for m in created if m.author_kind == "agent"]
    for reply in replies:
        send_message(platform, msg.external_id, reply.content, token=token or None)

    return {"handled": True, "replies": len(replies)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chat_bridge.py -k handle_inbound -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/chat_bridge.py tests/test_chat_bridge.py
git commit -m "feat(bridge): add handle_inbound routing (reuses workspace chat cascade)"
```

---

## Task 4: Webhook + channel-management routes + UI

**Files:**
- Modify: `dashboard/main.py`
- Create: `dashboard/templates/workspace_channels.html`
- Modify: `dashboard/templates/workspace_detail.html`
- Test: `tests/test_chat_bridge.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_bridge.py`:

```python
from fastapi.testclient import TestClient


@pytest.fixture()
def client(db, monkeypatch):
    import dashboard.main as dash
    monkeypatch.setattr("core.llm_providers.complete", _echo_complete)
    return TestClient(dash.app)


def test_webhook_unknown_platform_404(db, client):
    r = client.post("/bridge/nope/webhook", json={})
    assert r.status_code == 404


def test_webhook_slack_challenge_echoed(db, client):
    r = client.post("/bridge/slack/webhook", json={"type": "url_verification", "challenge": "ping"})
    assert r.status_code == 200
    assert r.json().get("challenge") == "ping"


def test_webhook_routes_inbound(db, client, monkeypatch):
    sent = []
    import core.chat_bridge as cbmod
    monkeypatch.setattr(cbmod, "send_message",
                        lambda platform, channel, text, **kw: sent.append((channel, text)) or True)
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="Win", selected_roles=["ceo"],
        )
        s.add(WorkspaceChannel(id="c1", workspace_id=ws.id, platform="telegram",
                               external_id="77", token_env="TELEGRAM_BOT_TOKEN"))
        s.commit()
    r = client.post("/bridge/telegram/webhook",
                    json={"message": {"chat": {"id": 77}, "text": "/ceo hi"}})
    assert r.status_code == 200
    assert r.json().get("handled") is True
    assert sent and sent[0][0] == "77"


def test_channel_link_and_remove_flow(db, client):
    with Session(db) as s:
        ws = wf.create_workspace(
            s, owner_email="o@x.com", name="Acme", company_description="",
            mission="", selected_roles=["ceo"],
        )
        ws_id = ws.id
    r = client.get(f"/workspaces/{ws_id}/channels")
    assert r.status_code == 200
    r = client.post(f"/workspaces/{ws_id}/channels", data={
        "platform": "telegram", "external_id": "555", "label": "Team",
        "token_env": "TELEGRAM_BOT_TOKEN"}, follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        ch = s.exec(select(WorkspaceChannel).where(
            WorkspaceChannel.workspace_id == ws_id)).one()
        assert ch.external_id == "555" and ch.active is True
        cid = ch.id
    r = client.post(f"/workspaces/{ws_id}/channels/{cid}/remove", follow_redirects=False)
    assert r.status_code == 303
    with Session(db) as s:
        assert s.get(WorkspaceChannel, cid).active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_bridge.py -k "webhook or channel_link" -v`
Expected: FAIL with 404s (routes don't exist)

- [ ] **Step 3: Add imports + routes to `dashboard/main.py`**

Add near the other `core` imports:
```python
from core import chat_bridge
from core.chat_bridge import BRIDGE_PLATFORMS
```
Add `WorkspaceChannel` to the `from core.state import (...)` block.

Add this block after the project routes (`workspace_project_archive`) and before the
legacy project routes:

```python
@app.post("/bridge/{platform}/webhook")
async def bridge_webhook(platform: str, request: Request) -> JSONResponse:
    if platform not in BRIDGE_PLATFORMS:
        raise HTTPException(404, "unknown platform")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        with Session(get_engine()) as s:
            result = chat_bridge.handle_inbound(s, platform, payload)
    except Exception as e:  # never 5xx — platforms retry-storm on errors
        logger.warning("bridge.webhook_error platform=%s err=%s", platform, e)
        result = {"handled": False, "error": str(e)}
    return JSONResponse(result, status_code=200)


@app.get("/workspaces/{workspace_id}/channels", response_class=HTMLResponse)
async def workspace_channels(request: Request, workspace_id: str) -> HTMLResponse:
    with Session(get_engine()) as s:
        ws = s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "workspace not found")
        channels = s.exec(
            select(WorkspaceChannel).where(WorkspaceChannel.workspace_id == workspace_id)
            .order_by(WorkspaceChannel.created_at.desc())
        ).all()
    return templates.TemplateResponse(request, "workspace_channels.html", {
        "ws": ws,
        "channels": channels,
        "platforms": BRIDGE_PLATFORMS,
    })


@app.post("/workspaces/{workspace_id}/channels")
async def workspace_channel_link(workspace_id: str, platform: str = Form(...),
                                 external_id: str = Form(...), label: str = Form(""),
                                 token_env: str = Form("")) -> RedirectResponse:
    with Session(get_engine()) as s:
        if s.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "workspace not found")
        if platform in BRIDGE_PLATFORMS and external_id.strip():
            s.add(WorkspaceChannel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                platform=platform,
                external_id=external_id.strip(),
                label=label.strip() or None,
                token_env=token_env.strip() or BRIDGE_PLATFORMS[platform]["token_env"],
            ))
            s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/channels", status_code=303)


@app.post("/workspaces/{workspace_id}/channels/{channel_id}/remove")
async def workspace_channel_remove(workspace_id: str, channel_id: str) -> RedirectResponse:
    with Session(get_engine()) as s:
        ch = s.get(WorkspaceChannel, channel_id)
        if ch is None or ch.workspace_id != workspace_id:
            raise HTTPException(404, "channel not found")
        ch.active = False
        s.add(ch)
        s.commit()
    return RedirectResponse(url=f"/workspaces/{workspace_id}/channels", status_code=303)
```

(`uuid`, `Workspace`, `select`, `Session`, `get_engine`, `templates`, `Form`,
`HTTPException`, `RedirectResponse`, `HTMLResponse`, `JSONResponse`, `Request`, `logger`
are all already imported.)

- [ ] **Step 4: Create `dashboard/templates/workspace_channels.html`**

```html
{% extends "_base.html" %}
{% block title %}{{ ws.name }} · Channels{% endblock %}
{% block content %}
<section class="max-w-3xl mx-auto px-6 lg:px-8 py-12">
  <a href="/workspaces/{{ ws.id }}" class="text-xs text-slate-500 hover:text-white">← {{ ws.name }}</a>
  <h1 class="text-2xl font-semibold text-white mt-1 mb-1">External channels</h1>
  <p class="text-slate-400 text-sm mb-6">Link a Telegram chat, Slack channel, or WhatsApp number. Messages addressed with <code class="text-violet-300">/agent</code> route to your team; replies come back here.</p>

  <div class="space-y-2 mb-8">
    {% for c in channels %}
      <div class="flex items-center justify-between border border-white/10 rounded-lg p-3 {{ 'opacity-50' if not c.active else '' }}">
        <div>
          <div class="text-white text-sm font-medium">{{ platforms[c.platform].label if c.platform in platforms else c.platform }} · {{ c.label or c.external_id }}</div>
          <div class="text-slate-500 text-xs">id {{ c.external_id }} · token env {{ c.token_env or '—' }} {{ '· inactive' if not c.active else '' }}</div>
        </div>
        {% if c.active %}
          <form method="post" action="/workspaces/{{ ws.id }}/channels/{{ c.id }}/remove">
            <button class="text-xs text-slate-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white transition">Remove</button>
          </form>
        {% endif %}
      </div>
    {% else %}
      <p class="text-slate-500 text-sm">No channels linked yet.</p>
    {% endfor %}
  </div>

  <h2 class="text-lg font-semibold text-white mb-3">Link a channel</h2>
  <form method="post" action="/workspaces/{{ ws.id }}/channels" class="space-y-3">
    <select name="platform" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
      {% for pid, p in platforms.items() %}<option value="{{ pid }}">{{ p.label }}</option>{% endfor %}
    </select>
    <input name="external_id" required placeholder="Chat / channel / phone id" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <input name="label" placeholder="Label (optional)" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <input name="token_env" placeholder="Token env var (optional, e.g. TELEGRAM_BOT_TOKEN)" class="w-full bg-ink-800 border border-white/10 rounded-md px-3 py-2 text-white">
    <button class="text-sm font-medium bg-grad-brand text-ink-950 px-5 py-2 rounded-md shadow-glow-v hover:shadow-glow-c transition">Link channel</button>
  </form>

  <div class="mt-8 text-xs text-slate-500">
    Webhook URLs: <code>POST /bridge/telegram/webhook</code>, <code>/bridge/slack/webhook</code>, <code>/bridge/whatsapp/webhook</code>.
  </div>
</section>
{% endblock %}
```

- [ ] **Step 5: Add a "Channels" link in `dashboard/templates/workspace_detail.html`**

In the `<div class="flex items-center gap-2">` action row, add (after the "Projects →" link, before "Team chat →"):
```html
      <a href="/workspaces/{{ ws.id }}/channels" class="text-xs text-emerald-300 border border-emerald-500/30 px-3 py-1.5 rounded-md hover:bg-emerald-500/10 transition">Channels →</a>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_chat_bridge.py -k "webhook or channel_link" -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add dashboard/main.py dashboard/templates/workspace_channels.html dashboard/templates/workspace_detail.html tests/test_chat_bridge.py
git commit -m "feat(bridge): add webhook + channel-management routes and UI"
```

---

## Task 5: Config + docs

**Files:**
- Modify: `.env.example`, `CLAUDE.md`

- [ ] **Step 1: Document env vars in `.env.example`**

Append to `.env.example`:

```
# ── External chat bridge (subsystem E) ───────────────────────
# Optional platform bot tokens — absent → outbound sends no-op (fail-open)
TELEGRAM_BOT_TOKEN=
SLACK_BOT_TOKEN=
WHATSAPP_TOKEN=
WHATSAPP_PHONE_ID=
```

- [ ] **Step 2: Document the layer in `CLAUDE.md`**

Append this bullet to the END of item 6 ("Workspace layer") sub-list (3-space indent + `- `):

```markdown
   - `core/chat_bridge.py` — external chat bridge (subsystem E): a platform registry (`telegram`, `slack`, `whatsapp`); `parse_inbound` normalizes webhook payloads (Slack URL-verification echoed); `handle_inbound` looks up the `WorkspaceChannel` mapping and routes the message through `workspace_chat.post_message` (reusing mention resolution + the bounded cascade), then replies back via a **fail-open** `send_message` (no token → no-op, like `notifier.SlackNotifier`). Routes: `POST /bridge/{platform}/webhook` (always 200), `/workspaces/{id}/channels` (link/list/remove). Unmapped inbound is dropped — no cross-workspace leakage.
```

- [ ] **Step 3: Run the full suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "chore(bridge): document platform tokens and the chat_bridge layer"
```

---

## Done — definition of complete

- `pytest tests/ -v` green, including `tests/test_chat_bridge.py`.
- A linked Telegram/Slack/WhatsApp channel routes `/<agent>` messages into workspace chat and sends replies back (outbound captured in tests; fail-open in prod without tokens).
- Webhooks always return 200; Slack URL-verification challenge echoed; unknown platform 404.
- Unmapped inbound is dropped; the internal chat engine (D) is reused unchanged.
