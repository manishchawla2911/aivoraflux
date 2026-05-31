from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    primary_goal: str


class UserStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[str]
    priority: Priority


class FunctionalRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    req_id: str
    description: str
    priority: Priority
    linked_stories: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    requirement: str


class PRD(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    problem_statement: str
    user_personas: list[UserPersona]
    user_stories: list[UserStory]
    functional_requirements: list[FunctionalRequirement]
    non_functional_requirements: list[NonFunctionalRequirement]
    out_of_scope: list[str]
    tech_constraints: Optional[str] = None
    success_metrics: list[str]
    edge_cases: list[str]


class ClarificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_brief: str
    client_name: str
    budget_range: str
    timeline_weeks: int
    preferred_stack: Optional[str] = None
    previous_answers: list[dict] = Field(default_factory=list)


class ClarificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "needs_clarification" | "prd_ready"
    clarification_questions: list[str] = Field(default_factory=list)
    prd: Optional[PRD] = None
