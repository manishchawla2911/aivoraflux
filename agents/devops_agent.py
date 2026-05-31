"""DevOps Agent — generates CI/CD pipelines, Dockerfiles, deployment scripts."""
from agents._builder_base import BuilderAgentBase
from schemas.task import AgentType


class DevOpsAgent(BuilderAgentBase):
    name = "devops_agent"
    agent_type = AgentType.DEVOPS
    branch_pattern = "agent/devops/{task_id}"
    prompt_filename = "devops_agent.md"
