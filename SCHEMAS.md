# Schema Reference — Agent I/O Contracts
> Implement these as Pydantic v2 models in the `schemas/` directory.
> Every agent input and output must be validated against these schemas.

---

## schemas/prd.py

```python
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class UserPersona(BaseModel):
    name: str
    description: str
    primary_goal: str

class UserStory(BaseModel):
    story_id: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[str]
    priority: Priority

class FunctionalRequirement(BaseModel):
    req_id: str
    description: str
    priority: Priority
    linked_stories: list[str] = []

class NonFunctionalRequirement(BaseModel):
    category: str  # e.g. "performance", "security", "accessibility"
    requirement: str

class PRD(BaseModel):
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
    raw_brief: str
    client_name: str
    budget_range: str
    timeline_weeks: int
    preferred_stack: Optional[str] = None
    previous_answers: list[dict] = []

class ClarificationOutput(BaseModel):
    status: str  # "needs_clarification" | "prd_ready"
    clarification_questions: list[str] = []
    prd: Optional[PRD] = None
```

---

## schemas/architecture.py

```python
from pydantic import BaseModel
from typing import Optional
from enum import Enum

class ComponentType(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    SERVICE = "service"
    DATABASE = "database"

class TechStack(BaseModel):
    frontend: str
    backend: str
    database: str
    auth: str
    hosting: str
    third_party_services: list[str] = []

class SystemComponent(BaseModel):
    name: str
    responsibility: str
    component_type: ComponentType

class DataFlow(BaseModel):
    from_component: str
    to_component: str
    description: str
    method: str  # "REST" | "webhook" | "queue" | "direct"

class DBColumn(BaseModel):
    name: str
    col_type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: Optional[str] = None  # "table.column"

class DBTable(BaseModel):
    name: str
    columns: list[DBColumn]
    indexes: list[str] = []

class APIContract(BaseModel):
    contract_id: str
    endpoint: str
    method: str
    auth_required: bool
    description: str
    request_body: Optional[dict] = None
    response_body: dict
    error_responses: list[dict] = []

class ArchitectureDecision(BaseModel):
    decision: str
    rationale: str
    alternatives_considered: list[str] = []

class RiskFlag(BaseModel):
    risk: str
    mitigation: str

class EnvironmentVariable(BaseModel):
    key: str
    description: str
    required: bool = True

class Architecture(BaseModel):
    tech_stack: TechStack
    system_design: list[SystemComponent]
    data_flows: list[DataFlow]
    database_schema: list[DBTable]
    api_contracts: list[APIContract]
    folder_structure: str  # ASCII tree
    environment_variables: list[EnvironmentVariable]
    architecture_decisions: list[ArchitectureDecision]
    risk_flags: list[RiskFlag] = []
```

---

## schemas/task.py

```python
from pydantic import BaseModel
from typing import Optional
from enum import Enum

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
    relevant_prd_sections: list[str]       # requirement IDs
    relevant_api_contracts: list[dict]      # full contract objects
    relevant_schema_tables: list[str]       # table names
    relevant_folder_structure: str          # ASCII subtree

class Task(BaseModel):
    task_id: str
    title: str
    description: str
    agent_type: AgentType
    depends_on: list[str] = []
    acceptance_criteria: list[str]
    files_to_create: list[str]
    files_to_modify: list[str] = []
    api_contracts_used: list[str] = []
    estimated_complexity: TaskComplexity
    context_package: ContextPackage

class TaskGraph(BaseModel):
    project_id: str
    tasks: list[Task]
    execution_order: list[list[str]]        # batches of task_ids

class BuilderOutput(BaseModel):
    task_id: str
    agent_type: AgentType
    files_created: list[dict]               # {path, line_count}
    tests_written: list[dict]               # {path, test_count}
    branch_name: str
    commit_sha: str
    notes: str = ""
    env_vars_required: list[str] = []       # integration agent only
```

---

## schemas/validation_report.py

```python
from pydantic import BaseModel
from enum import Enum

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class SecurityFinding(BaseModel):
    severity: Severity
    rule: str
    file: str
    line: int
    description: str
    fix_suggestion: str

class QualityFinding(BaseModel):
    finding_type: str                       # "complexity" | "dead_code" | "style"
    file: str
    line: int
    description: str

class SecretFinding(BaseModel):
    file: str
    line: int
    secret_type: str

class TestFailure(BaseModel):
    test_name: str
    error_message: str

class TestRunResult(BaseModel):
    passed: int
    failed: int
    skipped: int
    coverage_percent: float
    failures: list[TestFailure] = []

class TestReport(BaseModel):
    task_id: str
    tests_added: list[dict]                 # {path, test_names}
    test_run_result: TestRunResult
    status: str                             # "passed" | "failed"

class SecurityQualityReport(BaseModel):
    task_id: str
    security_findings: list[SecurityFinding] = []
    quality_findings: list[QualityFinding] = []
    secret_scan: dict                       # {secrets_found, findings}
    scores: dict                            # {security_score, quality_score}
    status: str                             # "passed" | "failed"
    blocking_findings: list[SecurityFinding] = []
```

---

## schemas/delivery.py

```python
from pydantic import BaseModel

class PRDCoverage(BaseModel):
    requirements_covered: list[str]
    requirements_missing: list[str]
    coverage_percent: float

class GateSummary(BaseModel):
    tests_passed: bool
    security_passed: bool
    quality_passed: bool
    all_files_present: bool

class FlagForHuman(BaseModel):
    flag_type: str
    description: str
    options: list[str]
    recommended_option: str

class ReviewSummary(BaseModel):
    project_id: str
    overall_status: str                     # "auto_approved" | "needs_human" | "failed"
    prd_coverage: PRDCoverage
    gate_summary: GateSummary
    flags_for_human: list[FlagForHuman] = []
    auto_approval_rationale: str = ""
    merge_ready: bool

class DocumentsGenerated(BaseModel):
    api_docs: str
    setup_guide: str
    architecture_overview: str
    runbook: str
    env_template: str

class DeliveryPackage(BaseModel):
    project_id: str
    project_name: str
    documents_generated: DocumentsGenerated
    proof_report_path: str
    deployment_scripts: list[str]
    delivery_package_path: str
```
