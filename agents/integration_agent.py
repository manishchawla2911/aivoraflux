"""Integration Agent — builds third-party service integrations."""
from agents._builder_base import BuilderAgentBase
from schemas.task import AgentType


class IntegrationAgent(BuilderAgentBase):
    name = "integration_agent"
    agent_type = AgentType.INTEGRATION
    branch_pattern = "agent/integration/{task_id}"
    prompt_filename = "integration_agent.md"
