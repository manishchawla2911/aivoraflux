# External Chat Bridge (Subsystem E) — Design

**Date:** 2026-05-31
**Status:** Approved (owner pre-delegated all decisions for this run)
**Cycle:** 4 of the `FEATURES_V1.md` "AI company" vision (E of D/G/E/F)

## Context

Cycles 1–3 shipped the Workspace foundation, internal chat (subsystem D), and CEO
spawning + cost estimator (subsystem G). `FEATURES_V1.md` item 4 asks: *"The owner can
talk to them by `/<agent's_name>` cmd in a telegram or whatsapp or slack."* This cycle
bridges those platforms to the existing internal chat engine.

## Goal & scope

An inbound message from Telegram / Slack / WhatsApp that addresses an agent with
`/<name>` (or `@Name`) is routed through the **existing** `workspace_chat.post_message`
(so mention resolution and the bounded agent-to-agent cascade are reused unchanged), and
each resulting agent reply is sent back to the originating platform. A workspace is linked
to an external channel via a `WorkspaceChannel` mapping.

**In scope**
- `WorkspaceChannel` data model (workspace ↔ platform channel).
- `core/chat_bridge.py`: a platform registry (telegram, slack, whatsapp) with
  `parse_inbound` (payload → normalized message), `send_message` (fail-open outbound,
  mirroring `SlackNotifier`), and `handle_inbound` (route → chat → reply back).
- Webhook routes `POST /bridge/{platform}/webhook` (always 200 to avoid retry storms;
  Slack URL-verification challenge echoed).
- Channel-management UI under a workspace (link/list/remove a channel).

**Out of scope (deferred / other cycles)**
- Rich media, attachments, threads, typing indicators, read receipts.
- Per-user identity/auth from the platform (the inbound sender is treated as "owner").
- Outbound-initiated campaigns (that is subsystem F).
- Voice (F).

## Key decisions

- **Reuse D, don't fork it.** Inbound routing calls `workspace_chat.post_message(
  author_kind="owner", content=text)`. The `/<name>`/`@Name` addressing, mention
  resolution, grounding, and bounded cascade all come for free. The bridge only
  translates payloads in/out.
- **Fail-open outbound.** `send_message` mirrors `SlackNotifier`: if the platform token
  env is unset it logs and no-ops (returns False) — never raises. So the system runs
  offline and tests need no credentials.
- **Webhooks always 200.** A malformed/unmapped/unauthified inbound returns 200 with a
  small JSON body (`handled: false`) so platforms don't retry-storm. Slack's
  `url_verification` challenge is echoed.
- **Channel mapping is explicit.** The owner links a workspace to a `(platform,
  external_id)` channel and names the token env var. Inbound is dropped (handled:false)
  when no mapping exists — no accidental cross-workspace leakage.

## Data model (`core/state.py`)

### `WorkspaceChannel` (new)
- `id: str` (PK)
- `workspace_id: str` (FK → `workspace.id`, indexed)
- `platform: str` — `telegram | slack | whatsapp`
- `external_id: str` (indexed) — Telegram chat id / Slack channel id / WhatsApp phone id
- `label: Optional[str]`
- `token_env: Optional[str]` — name of the env var holding the bot token (e.g. `TELEGRAM_BOT_TOKEN`)
- `active: bool = True`
- `created_at: datetime`

Lookup is by `(platform, external_id, active=True)`.

## Bridge module (`core/chat_bridge.py`)

`BRIDGE_PLATFORMS: dict` — per-platform metadata (label, default token env, send-API hint).

`@dataclass InboundMessage: external_id: str, text: str, sender: Optional[str],
challenge: Optional[str] = None` — `challenge` set for Slack URL verification.

- `parse_inbound(platform, payload) -> Optional[InboundMessage]` — defensive,
  platform-specific extraction:
  - **telegram**: `payload["message"]["chat"]["id"]` → external_id, `["message"]["text"]`.
  - **slack**: if `payload.get("type")=="url_verification"` → `InboundMessage("", "",
    None, challenge=payload["challenge"])`; else `payload["event"]["channel"]` +
    `["event"]["text"]`, ignoring bot/self events (`event.get("bot_id")` present → None).
  - **whatsapp**: dig `entry[0].changes[0].value.messages[0]` → `from` + `text.body`.
  - Returns `None` if the payload has no usable message.
- `send_message(platform, channel, text, *, token=None) -> bool` — fail-open httpx POST:
  - **telegram**: `https://api.telegram.org/bot{token}/sendMessage` `{chat_id, text}`.
  - **slack**: `https://slack.com/api/chat.postMessage` Bearer `{channel, text}`.
  - **whatsapp**: Meta Graph send (best-effort; needs phone-id config — documented).
  - Token resolved from `token` arg or `os.getenv(channel.token_env)`; if absent →
    log + return False (no raise). Network errors caught → False.
- `handle_inbound(session, platform, payload) -> dict`:
  1. `msg = parse_inbound(platform, payload)`. If `msg is None` → `{"handled": False}`.
  2. If `msg.challenge` → `{"challenge": msg.challenge}` (Slack verification).
  3. Find an active `WorkspaceChannel` by `(platform, msg.external_id)`. None →
     `{"handled": False, "reason": "unmapped"}`.
  4. `created = workspace_chat.post_message(session, channel.workspace_id,
     author_kind="owner", content=msg.text)`.
  5. For each agent reply in `created`, `send_message(platform, msg.external_id,
     reply.content, token=os.getenv(channel.token_env) if channel.token_env else None)`.
  6. Return `{"handled": True, "replies": <n agent replies>}`.

## Routes (`dashboard/main.py`)

| Route | Purpose |
|-------|---------|
| `POST /bridge/{platform}/webhook` | platform webhook; returns 200 always; echoes Slack challenge; body is `handle_inbound(...)` result |
| `GET /workspaces/{id}/channels` | list linked channels + a link form |
| `POST /workspaces/{id}/channels` | link a channel (platform, external_id, label, token_env) |
| `POST /workspaces/{id}/channels/{cid}/remove` | set `active=False` |

The webhook route validates `platform in BRIDGE_PLATFORMS` (else 404) and wraps
`handle_inbound` in try/except so it always returns 200 (a thrown error → `{"handled":
False, "error": "..."}` + 200). UI is a small `workspace_channels.html`; the workspace
detail page gains a "Channels →" link.

## Config

New env (documented in `.env.example` + `CLAUDE.md`): `TELEGRAM_BOT_TOKEN`,
`SLACK_BOT_TOKEN`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` (all optional; absent → outbound
no-ops fail-open).

## Testing (`tests/test_chat_bridge.py`)

All offline, `EMBEDDING_BACKEND=stub`, completion monkeypatched, `send_message`
monkeypatched to capture outbound (no network).
- **parse_inbound** — telegram/slack/whatsapp payloads parse to the right
  `external_id`/`text`; Slack `url_verification` yields a `challenge`; junk → `None`;
  Slack bot events ignored.
- **send_message fail-open** — with no token env set, returns False and does not raise.
- **handle_inbound routing** — a linked telegram channel + a `/ceo …` message creates a
  chat message, produces a CEO reply, and calls `send_message` with the reply text back
  to the same `external_id`; an unmapped channel → `{"handled": False}`.
- **webhook route** — `POST /bridge/telegram/webhook` returns 200 and routes; Slack
  challenge echoed; unknown platform → 404; a handler exception still yields 200.
- **channel management** — link a channel via `POST /workspaces/{id}/channels`, it shows
  in the list, remove sets `active=False`.

## File summary

**New:** `core/chat_bridge.py`, `dashboard/templates/workspace_channels.html`,
`tests/test_chat_bridge.py`.

**Edited:** `core/state.py` (1 table), `dashboard/main.py` (webhook + channel routes),
`dashboard/templates/workspace_detail.html` (link), `.env.example`, `CLAUDE.md`.

## Invariants preserved

- The internal chat engine (D) is reused unchanged — the bridge is a thin translation
  layer; mention resolution and the bounded cascade are not duplicated.
- Outbound is fail-open; no platform credential is required to run or test.
- Webhooks never 5xx on bad input (always 200) — no platform retry storms.
- Unmapped inbound is dropped — no cross-workspace leakage.
- Offline-testable: no network; `send_message` and completion are stubbed in tests.
