"""Frontend Agent — builds UI components, pages, state management via Claude Code subprocess."""
from agents._builder_base import BuilderAgentBase
from schemas.task import AgentType


class FrontendAgent(BuilderAgentBase):
    name = "frontend_agent"
    agent_type = AgentType.FRONTEND
    branch_pattern = "agent/frontend/{task_id}"
    prompt_filename = "frontend_agent.md"
