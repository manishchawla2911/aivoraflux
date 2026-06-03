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


def instantiate_member(
    session: Session,
    workspace_id: str,
    role_def: Dict,
    *,
    workspace_name: str = "",
    display_name: Optional[str] = None,
    system_prompt: Optional[str] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
    avatar_emoji: Optional[str] = None,
    order_index: int = 0,
    parent_member_id: Optional[str] = None,
    origin: str = "seed",
    guardrail_id: Optional[str] = None,
    owner_email: Optional[str] = None,
) -> WorkspaceMember:
    """Instantiate one catalog role into a real Agent + WorkspaceMember.

    Shared by create_workspace (origin="seed") and workspace_spawn (origin="spawned").
    `role_def` is any catalog dict from ROLE_CATALOG or SPAWN_ROLE_CATALOG.
    """
    display_name = (display_name or role_def["label"]).strip()
    description = f"{role_def['label']} of {workspace_name}" if workspace_name else role_def["label"]

    spec = AgentSpec(
        name=display_name,
        description=description,
        category=role_def["category"],
        system_prompt=system_prompt or role_def["default_system_prompt"],
        model_provider=model_provider or "anthropic",
        model_name=model_name or role_def["default_model"],
        temperature=0.7,
        max_tokens=2048,
        tools=list(role_def["suggested_tools"]),
        avatar_emoji=avatar_emoji or role_def["avatar_emoji"],
        crafted_mode="manual",
    )
    kwargs = spec.to_db_kwargs()
    kwargs["guardrail_profile_id"] = guardrail_id
    kwargs["owner_email"] = owner_email
    agent = Agent(**kwargs)
    session.add(agent)
    session.commit()
    session.refresh(agent)

    member = WorkspaceMember(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        agent_id=agent.id,
        role=role_def["id"],
        display_name=display_name,
        order_index=order_index,
        parent_member_id=parent_member_id,
        origin=origin,
        created_at=utcnow(),
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


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
        instantiate_member(
            session, ws.id, role, workspace_name=ws.name,
            display_name=ov.get("display_name"),
            system_prompt=ov.get("system_prompt"),
            model_provider=ov.get("model_provider"),
            model_name=ov.get("model_name"),
            avatar_emoji=ov.get("avatar_emoji"),
            order_index=order,
            origin="seed",
            guardrail_id=guardrail_id,
            owner_email=owner_email,
        )
        order += 1

    if ws.mission:
        workspace_memory.add_entry(
            session, ws.id, author="owner", kind="goal",
            content=ws.mission, tags=["mission"],
        )

    return ws
