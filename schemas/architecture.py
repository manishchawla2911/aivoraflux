from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ComponentType(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    SERVICE = "service"
    DATABASE = "database"


class TechStack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontend: str
    backend: str
    database: str
    auth: str
    hosting: str
    third_party_services: list[str] = Field(default_factory=list)


class SystemComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    responsibility: str
    component_type: ComponentType


class DataFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_component: str
    to_component: str
    description: str
    method: str  # "REST" | "webhook" | "queue" | "direct"


class DBColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    col_type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: Optional[str] = None  # "table.column"


class DBTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    columns: list[DBColumn]
    indexes: list[str] = Field(default_factory=list)


class APIContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    endpoint: str
    method: str
    auth_required: bool
    description: str
    request_body: Optional[dict] = None
    response_body: dict
    error_responses: list[dict] = Field(default_factory=list)


class ArchitectureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str
    mitigation: str


class EnvironmentVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    description: str
    required: bool = True


class Architecture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tech_stack: TechStack
    system_design: list[SystemComponent]
    data_flows: list[DataFlow]
    database_schema: list[DBTable]
    api_contracts: list[APIContract]
    folder_structure: str  # ASCII tree
    environment_variables: list[EnvironmentVariable]
    architecture_decisions: list[ArchitectureDecision]
    risk_flags: list[RiskFlag] = Field(default_factory=list)
