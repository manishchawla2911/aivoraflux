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
