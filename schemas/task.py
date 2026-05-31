from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AgentType(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    INTEGRATION = "integration"
    DEVOPS = "devops"


class TaskComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VALIDATING = "validating"
    DONE = "done"
    FAILED = "failed"
    ESCALATED = "escalated"


class ContextPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant_prd_sections: list[str]
    relevant_api_contracts: list[dict]
    relevant_schema_tables: list[str]
    relevant_folder_structure: str


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str
    description: str
    agent_type: AgentType
    depends_on: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str]
    files_to_create: list[str]
    files_to_modify: list[str] = Field(default_factory=list)
    api_contracts_used: list[str] = Field(default_factory=list)
    estimated_complexity: TaskComplexity
    context_package: ContextPackage


class TaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    tasks: list[Task]
    execution_order: list[list[str]]  # batches of task_ids


class BuilderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    agent_type: AgentType
    files_created: list[dict]  # {path, line_count}
    tests_written: list[dict]  # {path, test_count}
    branch_name: str
    commit_sha: str
    notes: str = ""
    env_vars_required: list[str] = Field(default_factory=list)
