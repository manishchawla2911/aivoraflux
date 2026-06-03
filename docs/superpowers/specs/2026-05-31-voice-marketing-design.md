# Voice Marketing Agent (Subsystem F) — Design

**Date:** 2026-05-31
**Status:** Approved (owner pre-delegated all decisions for this run)
**Cycle:** 5 of the `FEATURES_V1.md` "AI company" vision (F of D/G/E/F)

## Context

Cycles 1–4 shipped the Workspace foundation, internal chat (D), spawning + cost
estimator (G), and the external chat bridge (E). `FEATURES_V1.md` item 2 asks for a
**Marketing Agent** that interacts with real clients with **STT and TTS** capabilities
and **email + follow-up email** capabilities. This cycle builds the marketing engine.

## Goal & scope

The workspace's Marketing member can: manage **contacts** (leads/clients), **draft and
send outreach emails** (drafted by the marketing agent via the shared
`run_agent_completion`), and **schedule + send follow-ups**. Pluggable **voice** (STT/TTS)
and **email** provider abstractions are added, each fail-open to an offline stub so the
system runs and tests with no credentials. A small voice demo endpoint exercises STT/TTS.

**In scope**
- `core/voice.py` — pluggable `transcribe(audio) -> text` (STT) and `synthesize(text)
  -> audio bytes` (TTS); backends `stub` (default), `openai`, `elevenlabs`; fail-open.
- `core/email_sender.py` — fail-open `send_email(to, subject, body) -> bool`; backends
  `stub` (default), `smtp`; mirrors `notifier.SlackNotifier` fail-open.
- Data model: `MarketingContact`, `OutreachMessage`.
- `core/marketing.py` — `draft_outreach` (agent-drafted body), `send_outreach`,
  `due_followups`, `send_followup` (scheduled follow-up cadence).
- Routes + UI: contacts, launch outreach, outreach log, run due follow-ups, voice demo.

**Out of scope (deferred)**
- Real telephony / live phone calls (Twilio/SIP) — the voice abstraction provides the
  STT/TTS capability and a demo; wiring a phone number is a later infra cycle.
- Inbound email parsing / reply detection (status is owner-updated this cycle).
- A background scheduler daemon — follow-ups are sent when the owner (or a future cron)
  triggers `due_followups`; the function is the seam a scheduler would call.

## Key decisions

- **Reuse the agent path for drafting.** `draft_outreach` calls the shared
  `agent_runtime.run_agent_completion` on the workspace's Marketing member, so drafts are
  guardrailed, grounded in shared memory (it's a workspace member), and observable.
- **Fail-open providers.** Voice and email both degrade to a deterministic offline stub
  when no key/SDK is present (same philosophy as `embeddings`, `llm_providers`,
  `chat_bridge`). Tests need no credentials; nothing raises.
- **Follow-ups are data-driven, not a daemon.** `OutreachMessage.next_followup_at` +
  `followup_count` drive a deterministic `due_followups(now)` query; `send_followup`
  drafts/sends and reschedules. A cron or the existing sweep loop can call it later.
- **Deterministic stubs.** Stub TTS returns bytes derived from the text; stub STT returns
  a fixed transcript; both are deterministic so tests assert behavior without audio.

## Data model (`core/state.py`)

### `MarketingContact` (new)
- `id: str` (PK)
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `name: str`
- `email: Optional[str]`
- `company: Optional[str]`
- `notes: Optional[str]`
- `status: str = "lead"` — `lead | contacted | replied | won | lost`
- `created_at: datetime`

### `OutreachMessage` (new)
- `id: str` (PK)
- `workspace_id: str` (FK, indexed)
- `contact_id: str` (FK → `marketing_contact.id`, indexed)
- `member_id: Optional[str]` — the Marketing `WorkspaceMember.id` that drafted it
- `kind: str = "outreach"` — `outreach | followup`
- `subject: str`
- `body: str`
- `channel: str = "email"`
- `send_status: str = "drafted"` — `drafted | sent | failed`
- `followup_count: int = 0`
- `next_followup_at: Optional[datetime]`
- `created_at: datetime`

## Voice provider (`core/voice.py`)

`VOICE_BACKEND` env selects the backend; fail-open to `stub`.
- `transcribe(audio: bytes, *, backend=None) -> str` — STT. `stub` → a deterministic
  string (e.g. `f"[transcript:{len(audio)} bytes]"`); `openai` (Whisper) / `elevenlabs`
  guarded imports; failure → stub with a logged warning.
- `synthesize(text: str, *, backend=None) -> bytes` — TTS. `stub` → deterministic bytes
  (e.g. `("AUDIO:" + text).encode()` or a hash-derived blob); real backends guarded;
  failure → stub.
- Mirrors the `embeddings.py` backend-select + fail-open structure.

## Email provider (`core/email_sender.py`)

- `send_email(to: str, subject: str, body: str, *, backend=None) -> bool` — fail-open.
  `EMAIL_BACKEND` env: `stub` (default; logs + returns True so flows complete offline) |
  `smtp` (uses `SMTP_HOST/PORT/USER/PASSWORD/FROM`; on any error → logs + False). No
  credentials → stub. Never raises. (Stub returns True so the outreach flow records a
  "sent" status in demos; real backends return their true result.)

## Marketing engine (`core/marketing.py`)

- `draft_outreach(session, member, contact, goal, *, kind="outreach",
  prior_body=None) -> (subject, body)` — builds a prompt ("Write a concise outreach email
  to {contact.name} at {contact.company} about: {goal}…"; for follow-ups include
  `prior_body`), calls `run_agent_completion` on the member's Agent, and derives a subject
  (first line / heuristic) + body from the result. Deterministic under the test echo-fake.
- `send_outreach(session, workspace_id, member, contact, goal, *,
  followup_days=3) -> OutreachMessage` — draft → `email_sender.send_email(contact.email…)`
  → persist `OutreachMessage` (`kind="outreach"`, `send_status` from the send result,
  `next_followup_at = utcnow()+followup_days`), set `contact.status="contacted"`.
- `due_followups(session, workspace_id, now=None) -> list[OutreachMessage]` — outreach
  whose `next_followup_at <= now`, `send_status="sent"`, and `followup_count < MAX`.
- `send_followup(session, member, outreach, *, followup_days=3) -> OutreachMessage` —
  draft a follow-up (referencing the prior body), send, persist a new `followup`
  `OutreachMessage`, increment the chain's `followup_count`, reschedule/stop at MAX.

`MARKETING_MAX_FOLLOWUPS` (default 2), `MARKETING_FOLLOWUP_DAYS` (default 3) via env.

## Routes & UI (`dashboard/main.py`)

| Route | Purpose |
|-------|---------|
| `GET /workspaces/{id}/marketing` | contacts + outreach log + "run due follow-ups" button |
| `POST /workspaces/{id}/marketing/contacts` | add a contact |
| `POST /workspaces/{id}/marketing/outreach` | launch outreach (contact_id, goal) → draft+send+schedule |
| `POST /workspaces/{id}/marketing/followups/run` | send all due follow-ups for the workspace |
| `POST /workspaces/{id}/marketing/voice/preview` | TTS demo: text → returns synthesized stub audio (bytes) |

Outreach uses the workspace's Marketing member (role `marketing`); if none exists, the
route flashes that a Marketing agent is required. New template `workspace_marketing.html`;
the workspace detail page gains a "Marketing →" link.

## Config

New env (documented in `.env.example` + `CLAUDE.md`): `VOICE_BACKEND` (stub),
`EMAIL_BACKEND` (stub), `SMTP_HOST/PORT/USER/PASSWORD/FROM`, `MARKETING_MAX_FOLLOWUPS`
(2), `MARKETING_FOLLOWUP_DAYS` (3).

## Testing (`tests/test_marketing.py`)

All offline, `EMBEDDING_BACKEND=stub`, `VOICE_BACKEND=stub`, `EMAIL_BACKEND=stub`,
completion monkeypatched.
- **voice stub** — `synthesize` returns deterministic non-empty bytes; `transcribe`
  returns a deterministic string; unknown backend falls back to stub.
- **email stub fail-open** — `send_email` returns True under stub and never raises; an
  `smtp` backend with no config returns False, no raise.
- **draft_outreach** — produces a subject+body grounded by the member (assert the
  echo-fake's system prompt reached it; subject derived).
- **send_outreach** — persists an `OutreachMessage` (`sent` under stub email), sets
  `contact.status="contacted"`, schedules `next_followup_at`.
- **follow-up cadence** — `due_followups` returns a sent outreach whose
  `next_followup_at` is in the past; `send_followup` creates a `followup` message,
  increments `followup_count`, and stops at `MARKETING_MAX_FOLLOWUPS`.
- **routes** — `TestClient`: add contact → launch outreach (creates a sent message) →
  run follow-ups; voice preview returns audio bytes; outreach with no Marketing member is
  handled gracefully (no 500).

## File summary

**New:** `core/voice.py`, `core/email_sender.py`, `core/marketing.py`,
`dashboard/templates/workspace_marketing.html`, `tests/test_marketing.py`.

**Edited:** `core/state.py` (2 tables), `dashboard/main.py` (marketing routes),
`dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

## Invariants preserved

- Marketing drafts go through the shared guardrailed `run_agent_completion` (grounded,
  observable) — no second LLM path.
- Voice and email are fail-open; no credential is required to run or test.
- Follow-ups are deterministic and bounded by `MARKETING_MAX_FOLLOWUPS` — no unbounded
  email loops.
- Offline-testable: stubs for voice/email/completion; no network.
