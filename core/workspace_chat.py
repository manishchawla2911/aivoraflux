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
