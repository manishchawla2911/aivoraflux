"""CEO dynamic spawning: a project's team is created at runtime.

The CEO spawns a Project Manager per project; the PM spawns developers and an auditor
sized to the brief by a deterministic planner. Spawned members are ordinary Agent +
WorkspaceMember rows (via workspace_factory.instantiate_member) so they inherit run,
guardrails, chat, and observability. Team size is plan-capped — no runaway creation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlmodel import Session, select

from core.state import Workspace, WorkspaceMember, WorkspaceProject
from core.workspace_factory import _default_guardrail_profile_id, instantiate_member


SPAWN_ROLE_CATALOG: List[Dict] = [
    {
        "id": "project_manager",
        "label": "Project Manager",
        "avatar_emoji": "🗂️",
        "category": "ops",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["slack_post", "calendar", "github"],
        "default_system_prompt": (
            "You are a Project Manager spawned to deliver a specific project. You break "
            "the brief into tasks, coordinate the developers and auditor assigned to you, "
            "track progress, surface blockers, and keep delivery aligned with the brief "
            "and the company mission. Be concrete and outcome-focused."
        ),
    },
    {
        "id": "developer",
        "label": "Developer",
        "avatar_emoji": "💻",
        "category": "coding",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["github", "code_interpreter", "file_ops"],
        "default_system_prompt": (
            "You are a Developer spawned to implement assigned tasks for a project. You "
            "write correct, tested, maintainable code, ask for clarification when a task "
            "is ambiguous, and report what you changed. Prefer simple, working solutions."
        ),
    },
    {
        "id": "auditor",
        "label": "Auditor",
        "avatar_emoji": "🔍",
        "category": "coding",
        "default_model": "claude-sonnet-4-6",
        "suggested_tools": ["github", "code_interpreter", "sql_query"],
        "default_system_prompt": (
            "You are an Auditor spawned to review a project's work for quality, security, "
            "and compliance. You check correctness, flag risks and policy violations, and "
            "give clear, actionable findings. Be rigorous but fair."
        ),
    },
]


def get_spawn_role(role_id: str) -> Optional[Dict]:
    for r in SPAWN_ROLE_CATALOG:
        if r["id"] == role_id:
            return r
    return None


@dataclass
class TeamPlan:
    num_developers: int
    with_auditor: bool

    @property
    def team_size(self) -> int:
        # PM + developers + (auditor if present)
        return 1 + self.num_developers + (1 if self.with_auditor else 0)


def plan_team(brief: str) -> TeamPlan:
    """Deterministically size a delivery team from the brief length."""
    n = len(brief or "")
    if n < 120:
        num_developers = 1
    elif n < 400:
        num_developers = 2
    else:
        num_developers = 3
    # Safety-first: every project gets an auditor.
    return TeamPlan(num_developers=num_developers, with_auditor=True)


def _next_order_index(session: Session, workspace_id: str) -> int:
    rows = session.exec(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    ).all()
    return len(rows)


def spawn_member(session: Session, workspace_id: str, role_id: str, *,
                 parent_member_id: Optional[str] = None,
                 display_name: Optional[str] = None) -> WorkspaceMember:
    """Spawn one member of a spawnable role into the workspace."""
    role = get_spawn_role(role_id)
    if role is None:
        raise ValueError(f"unknown spawn role: {role_id}")
    ws = session.get(Workspace, workspace_id)
    guardrail_id = _default_guardrail_profile_id(session)
    return instantiate_member(
        session, workspace_id, role,
        workspace_name=ws.name if ws else "",
        display_name=display_name,
        order_index=_next_order_index(session, workspace_id),
        parent_member_id=parent_member_id,
        origin="spawned",
        guardrail_id=guardrail_id,
        owner_email=ws.owner_email if ws else None,
    )


def spawn_project_team(session: Session, workspace_id: str,
                       project: WorkspaceProject, *,
                       plan: Optional[TeamPlan] = None) -> Dict:
    """CEO -> PM -> developers/auditor. Sets project.pm_member_id; returns the team."""
    plan = plan or plan_team(project.brief)

    ceo = session.exec(
        select(WorkspaceMember).where(
            (WorkspaceMember.workspace_id == workspace_id)
            & (WorkspaceMember.role == "ceo")
            & (WorkspaceMember.origin == "seed")
        ).order_by(WorkspaceMember.order_index)
    ).first()
    ceo_id = ceo.id if ceo else None

    pm = spawn_member(session, workspace_id, "project_manager",
                      parent_member_id=ceo_id, display_name=f"PM · {project.name}")
    project.pm_member_id = pm.id
    session.add(project)
    session.commit()

    developers = [
        spawn_member(session, workspace_id, "developer", parent_member_id=pm.id,
                     display_name=f"Developer {i + 1} · {project.name}")
        for i in range(plan.num_developers)
    ]
    auditors = []
    if plan.with_auditor:
        auditors.append(spawn_member(session, workspace_id, "auditor",
                                     parent_member_id=pm.id,
                                     display_name=f"Auditor · {project.name}"))
    return {"pm": pm, "developers": developers, "auditors": auditors}
