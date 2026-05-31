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
