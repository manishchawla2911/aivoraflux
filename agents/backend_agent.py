"""Backend Agent — builds APIs, models, business logic via Claude Code subprocess."""
from agents._builder_base import BuilderAgentBase
from schemas.task import AgentType


class BackendAgent(BuilderAgentBase):
    name = "backend_agent"
    agent_type = AgentType.BACKEND
    branch_pattern = "agent/backend/{task_id}"
    prompt_filename = "backend_agent.md"
