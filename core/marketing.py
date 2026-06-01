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
    contact_name = contact.name if contact else "there"
    contact_company = contact.company if contact else None
    instr = f"Write a concise, friendly {'follow-up ' if kind == 'followup' else ''}outreach email to {contact_name}"
    if contact_company:
        instr += f" at {contact_company}"
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
