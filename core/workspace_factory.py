"""Build a workspace: instantiate role templates into real Agents + members.

Pure functions over a passed Session (mirrors agent_factory's separation from the
web layer). Reuses agent_factory.AgentSpec.to_db_kwargs so workspace agents are
identical in shape to Studio agents and inherit every downstream capability.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from sqlmodel import Session, select

from core.agent_factory import AgentSpec
from core.state import Agent, GuardrailProfile, Workspace, WorkspaceMember, utcnow
from core.workspace_roles import get_role
from core import workspace_memory


def _default_guardrail_profile_id(session: Session) -> str:
    """Return (creating if needed) the 'Standard' guardrail profile id.

    Mirrors dashboard.main._default_guardrail_profile so factory-built agents are
    guardrailed exactly like Studio-built ones, without importing the web module.
    """
    gp = session.exec(
        select(GuardrailProfile).where(GuardrailProfile.name == "Standard")
    ).first()
    if gp is None:
        gp = GuardrailProfile(id=str(uuid.uuid4()), name="Standard",
                              description="Default — all SOTA layers active.")
        session.add(gp)
        session.commit()
        session.refresh(gp)
    return gp.id


def create_workspace(
    session: Session,
    *,
    owner_email: Optional[str],
    name: str,
    company_description: str,
    mission: str,
    selected_roles: List[str],
    member_overrides: Optional[Dict[str, Dict]] = None,
) -> Workspace:
    """Create a workspace, instantiate selected roles as Agents+members, seed mission."""
    member_overrides = member_overrides or {}

    ws = Workspace(
        id=str(uuid.uuid4()),
        owner_email=owner_email,
        name=name.strip() or "Untitled Workspace",
        company_description=(company_description or "").strip() or None,
        mission=(mission or "").strip() or None,
        status="active",
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)

    guardrail_id = _default_guardrail_profile_id(session)

    order = 0
    for role_id in selected_roles:
        role = get_role(role_id)
        if role is None:
            continue
        ov = member_overrides.get(role_id, {})
        display_name = (ov.get("display_name") or role["label"]).strip()

        spec = AgentSpec(
            name=display_name,
            description=f"{role['label']} of {ws.name}",
            category=role["category"],
            system_prompt=ov.get("system_prompt") or role["default_system_prompt"],
            model_provider=ov.get("model_provider") or "anthropic",
            model_name=ov.get("model_name") or role["default_model"],
            temperature=0.7,
            max_tokens=2048,
            tools=list(role["suggested_tools"]),
            avatar_emoji=ov.get("avatar_emoji") or role["avatar_emoji"],
            crafted_mode="manual",
        )
        kwargs = spec.to_db_kwargs()
        kwargs["guardrail_profile_id"] = guardrail_id
        kwargs["owner_email"] = owner_email
        agent = Agent(**kwargs)
        session.add(agent)
        session.commit()
        session.refresh(agent)

        session.add(WorkspaceMember(
            id=str(uuid.uuid4()),
            workspace_id=ws.id,
            agent_id=agent.id,
            role=role_id,
            display_name=display_name,
            order_index=order,
            created_at=utcnow(),
        ))
        session.commit()
        order += 1

    if ws.mission:
        workspace_memory.add_entry(
            session, ws.id, author="owner", kind="goal",
            content=ws.mission, tags=["mission"],
        )

    return ws
