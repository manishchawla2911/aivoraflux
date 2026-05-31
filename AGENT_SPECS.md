# Agent Specifications
> Each agent is a self-contained unit with a defined role, input schema, output schema, tools, and failure behaviour.
> Reference MASTER_PLAN.md for the communication protocol and overall architecture.

---

## Agent Index

| # | Agent | Tier | Triggered by | Output |
|---|---|---|---|---|
| 1 | Clarification Agent | Intake | New project created | PRD draft |
| 2 | Architect Agent | Thinking | PRD approved | Architecture doc |
| 3 | Task Planner Agent | Thinking | Architecture approved | Task graph JSON |
| 4 | Backend Agent | Builder | Task planner (backend tasks) | Code + unit tests |
| 5 | Frontend Agent | Builder | Task planner (frontend tasks) | Components + tests |
| 6 | Integration Agent | Builder | Task planner (integration tasks) | Integration code |
| 7 | DevOps Agent | Builder | Task planner (infra tasks) | CI/CD + Docker |
| 8 | Test Writer Agent | Validation | Each builder task completes | Test suite + results |
| 9 | Security & Quality Agent | Validation | Each builder task completes | Security/quality report |
| 10 | Review Agent | Review | All validation complete | Review summary |
| 11 | Docs & Delivery Agent | Delivery | Human approves review | Full delivery package |

---

## Agent 1 — Clarification Agent

**Role**: Converts a vague client brief into a structured, unambiguous PRD. Asks targeted follow-up questions until all ambiguities are resolved.

**Model**: `claude-sonnet-4-6`

**Triggered by**: New project intake form submission

**Max iterations**: 3 rounds of clarification before flagging human

### Input Schema
```json
{
  "raw_brief": "string — client's free-text description",
  "client_name": "string",
  "budget_range": "string — e.g. $5k–$15k",
  "timeline_weeks": "integer",
  "preferred_stack": "string | null",
  "previous_answers": "array of {question, answer} — empty on first run"
}
```

### Output Schema
```json
{
  "status": "needs_clarification | prd_ready",
  "clarification_questions": "array of strings — empty if prd_ready",
  "prd": {
    "project_name": "string",
    "problem_statement": "string",
    "user_personas": "array of {name, description, primary_goal}",
    "user_stories": "array of {as_a, i_want, so_that, acceptance_criteria: []}",
    "functional_requirements": "array of {id, description, priority: high|med|low}",
    "non_functional_requirements": "array of {category, requirement}",
    "out_of_scope": "array of strings",
    "tech_constraints": "string | null",
    "success_metrics": "array of strings",
    "edge_cases": "array of strings"
  }
}
```

### Behaviour Rules
- Never invent requirements. Only extract from what the client has said.
- If a requirement is ambiguous, add it to `clarification_questions` — do not assume.
- Maximum 5 questions per round. Prioritise questions by impact on architecture.
- If after 3 rounds critical ambiguities remain, set `status: needs_clarification` and flag human.

---

## Agent 2 — Architect Agent

**Role**: Reads the approved PRD and produces a complete technical architecture. Makes all stack decisions, defines all contracts, produces the build plan.

**Model**: `claude-opus-4-5` (extended thinking enabled)

**Triggered by**: Human PRD approval

**Runs**: Once per project

### Input Schema
```json
{
  "prd": "PRD object (full schema from Agent 1)",
  "budget_usd": "integer",
  "timeline_weeks": "integer",
  "deployment_target": "vercel | aws | gcp | self-hosted"
}
```

### Output Schema
```json
{
  "tech_stack": {
    "frontend": "string",
    "backend": "string",
    "database": "string",
    "auth": "string",
    "hosting": "string",
    "third_party_services": "array of strings"
  },
  "system_design": {
    "components": "array of {name, responsibility, type: frontend|backend|service}",
    "data_flow": "array of {from, to, description, method: REST|webhook|queue}"
  },
  "database_schema": {
    "tables": "array of {name, columns: [{name, type, nullable, pk, fk}], indexes: []}"
  },
  "api_contracts": "array of {endpoint, method, auth_required, request_body, response_body, errors}",
  "folder_structure": "string — ASCII tree of the full project directory",
  "environment_variables": "array of {key, description, required}",
  "architecture_decisions": "array of {decision, rationale, alternatives_considered}",
  "risk_flags": "array of {risk, mitigation}"
}
```

### Behaviour Rules
- Always justify stack decisions in `architecture_decisions`. No arbitrary choices.
- Define API contracts fully before any builder agent runs. Contracts are immutable once approved.
- Flag any requirement in the PRD that is architecturally risky or underspecified.
- Prefer simple, proven stacks over cutting-edge ones unless the PRD demands otherwise.

---

## Agent 3 — Task Planner Agent

**Role**: Breaks the architecture into a dependency-ordered, parallelisable task graph. Assigns each task to the correct builder agent.

**Model**: `claude-sonnet-4-6`

**Triggered by**: Human architecture approval

**Runs**: Once per project

### Input Schema
```json
{
  "architecture": "Architecture object (full schema from Agent 2)",
  "prd": "PRD object"
}
```

### Output Schema
```json
{
  "tasks": "array of Task objects",
  "execution_order": "array of arrays — each inner array is a parallel batch"
}
```

### Task Object Schema
```json
{
  "task_id": "string — e.g. BE-001, FE-001, INT-001, OPS-001",
  "title": "string",
  "description": "string — what to build, not how",
  "agent_type": "backend | frontend | integration | devops",
  "depends_on": "array of task_ids — empty means can start immediately",
  "acceptance_criteria": "array of strings — must match PRD user stories",
  "files_to_create": "array of file paths",
  "files_to_modify": "array of file paths",
  "api_contracts_used": "array of endpoint identifiers",
  "estimated_complexity": "low | medium | high",
  "context_package": {
    "relevant_prd_sections": "array of requirement IDs",
    "relevant_api_contracts": "array of contract objects",
    "relevant_schema_tables": "array of table names",
    "relevant_folder_structure": "string — only the subtree this task touches"
  }
}
```

### Behaviour Rules
- Every task must map to at least one PRD requirement. No orphan tasks.
- Tasks in the same parallel batch must have zero file overlap.
- Frontend tasks must never start before the API contracts they depend on are validated.
- The `context_package` must include everything an agent needs — assume it has no other context.
- Split large tasks. No task should touch more than 8 files.

---

## Agent 4 — Backend Agent

**Role**: Builds backend code — APIs, database models, business logic, auth, background jobs.

**Model**: Claude Code CLI

**Triggered by**: Task Planner — for tasks with `agent_type: backend`

**Constraint**: Works only within its assigned task's `files_to_create` and `files_to_modify`. Never touches frontend files.

### Input (injected as CLAUDE.md into Claude Code context)
```
PROJECT CONTEXT:
- Tech stack: {backend_stack}
- Database: {database}

TASK: {task_id} — {title}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria as numbered list}

FILES TO CREATE:
{files_to_create}

API CONTRACTS TO IMPLEMENT:
{api_contracts — full JSON}

DATABASE SCHEMA (relevant tables):
{schema_tables — full JSON}

FOLDER STRUCTURE (your scope only):
{relevant_folder_structure}

RULES:
1. Implement every acceptance criterion. Do not skip any.
2. Every function must have a docstring.
3. Every API endpoint must have input validation and error handling.
4. Never hardcode secrets. Use environment variables.
5. Write a unit test for every function with business logic.
6. Output only the files listed in FILES TO CREATE. Do not create additional files.
7. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "notes": ""}
```

### Output Schema
```json
{
  "task_id": "string",
  "files_created": "array of {path, line_count}",
  "tests_written": "array of {path, test_count}",
  "branch_name": "string",
  "commit_sha": "string",
  "notes": "string — any decisions made, assumptions, things to flag"
}
```

---

## Agent 5 — Frontend Agent

**Role**: Builds UI components, pages, state management, API integrations.

**Model**: Claude Code CLI

**Triggered by**: Task Planner — for tasks with `agent_type: frontend`

**Constraint**: Never modifies backend files. API calls only through the approved contracts.

### Input (injected as CLAUDE.md)
```
PROJECT CONTEXT:
- Frontend stack: {frontend_stack}
- Design system: {design_tokens if available, else "use Tailwind defaults"}

TASK: {task_id} — {title}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria as numbered list}

FILES TO CREATE:
{files_to_create}

API CONTRACTS TO CONSUME:
{api_contracts — full JSON}

RULES:
1. All API calls must use the exact endpoints and request/response shapes in API CONTRACTS.
2. Handle loading, error, and empty states for every async operation.
3. No hardcoded strings visible to users — use a constants file.
4. Components must be accessible: semantic HTML, ARIA labels where needed.
5. Write an integration test for every page/view.
6. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "notes": ""}
```

### Output Schema
Same structure as Backend Agent output.

---

## Agent 6 — Integration Agent

**Role**: Implements third-party service integrations — payments, notifications, CRMs, OAuth, webhooks.

**Model**: Claude Code CLI

**Triggered by**: Task Planner — for tasks with `agent_type: integration`

### Input (injected as CLAUDE.md)
```
PROJECT CONTEXT:
- Backend stack: {backend_stack}

TASK: {task_id} — {title}

INTEGRATION TARGET: {service_name}
INTEGRATION TYPE: {payment | notification | crm | oauth | webhook | other}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

FILES TO CREATE:
{files_to_create}

RULES:
1. Use the official SDK if available. Do not use raw HTTP unless no SDK exists.
2. Implement webhook signature verification for all inbound webhooks.
3. All credentials via environment variables. List required env vars in your notes.
4. Implement idempotency for payment and order operations.
5. Write sandbox/test-mode tests — never use live credentials in tests.
6. Handle rate limits and service unavailability gracefully.
7. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "env_vars_required": [], "notes": ""}
```

---

## Agent 7 — DevOps Agent

**Role**: Generates CI/CD pipelines, Dockerfiles, deployment scripts, environment configs, monitoring setup.

**Model**: Claude Code CLI

**Triggered by**: Task Planner — for tasks with `agent_type: devops`

**Constraint**: Knows nothing about application logic. Works only from the architecture's `tech_stack` and `deployment_target`.

### Input (injected as CLAUDE.md)
```
PROJECT CONTEXT:
- Tech stack: {full tech_stack object}
- Deployment target: {deployment_target}
- Environment: development | staging | production

TASK: {task_id} — {title}

FILES TO CREATE:
{files_to_create}

RULES:
1. Multi-stage Docker builds. Final image must be minimal (alpine or distroless).
2. GitHub Actions: lint → test → build → deploy. Each stage must pass before next runs.
3. Secrets via environment variables injected at runtime. Never baked into image.
4. Health check endpoints must be wired into container config.
5. Staging and production must be separate environments with separate secrets.
6. At the end, output JSON: {"files_created": [], "notes": ""}
```

---

## Agent 8 — Test Writer Agent

**Role**: Reads task spec and generated code. Writes tests it cannot find. Runs the full test suite and reports results.

**Model**: `claude-sonnet-4-6` + shell tool

**Triggered by**: Every builder agent task completion

**Runs in**: Same repo branch as the builder task

### Input Schema
```json
{
  "task_id": "string",
  "task_spec": "Task object",
  "files_created": "array of file paths",
  "existing_tests": "array of file paths"
}
```

### Tools available
- `read_file(path)` — read any file in the project
- `write_file(path, content)` — write test files only
- `run_command(cmd)` — run test commands (pytest, vitest, etc.)

### Output Schema
```json
{
  "task_id": "string",
  "tests_added": "array of {path, test_names: []}",
  "test_run_result": {
    "passed": "integer",
    "failed": "integer",
    "skipped": "integer",
    "coverage_percent": "float",
    "failures": "array of {test_name, error_message}"
  },
  "status": "passed | failed"
}
```

### Behaviour Rules
- Read every generated file before writing tests. Understand what it does.
- Cover every acceptance criterion with at least one test.
- Generate edge case tests from the PRD's `edge_cases` field.
- Do not rewrite tests the builder agent already wrote — only fill gaps.
- If tests fail: attempt one fix. If still failing, set `status: failed` — do not loop.

---

## Agent 9 — Security & Quality Agent

**Role**: Runs static analysis, secret scanning, OWASP checks, and code quality scoring on all generated code.

**Model**: `claude-sonnet-4-6` + shell tool

**Triggered by**: Every builder agent task completion (runs in parallel with Test Writer Agent)

### Input Schema
```json
{
  "task_id": "string",
  "files_created": "array of file paths",
  "agent_type": "backend | frontend | integration | devops"
}
```

### Tools available
- `run_command(cmd)` — runs Semgrep, Bandit, ESLint, etc.
- `read_file(path)` — reads any file

### Output Schema
```json
{
  "task_id": "string",
  "security_findings": "array of {severity: critical|high|med|low, rule, file, line, description, fix_suggestion}",
  "quality_findings": "array of {type: complexity|dead_code|style, file, line, description}",
  "secret_scan": {
    "secrets_found": "boolean",
    "findings": "array of {file, line, type}"
  },
  "scores": {
    "security_score": "integer 0–100",
    "quality_score": "integer 0–100"
  },
  "status": "passed | failed",
  "blocking_findings": "array — critical/high severity items that must be fixed before merge"
}
```

### Pass Criteria
- Zero secrets found
- Zero critical security findings
- Zero high security findings (or all have documented exceptions)
- Security score ≥ 75
- Quality score ≥ 70

---

## Agent 10 — Review Agent

**Role**: Aggregates all validation outputs across all tasks. Checks completeness against PRD. Produces a decision summary for the human. Auto-approves if all gates pass.

**Model**: `claude-opus-4-5`

**Triggered by**: All tasks in the project's final batch complete validation

### Input Schema
```json
{
  "project_id": "string",
  "prd": "PRD object",
  "architecture": "Architecture object",
  "task_results": "array of {task_id, builder_output, test_result, security_result}",
  "all_files": "array of file paths in the project"
}
```

### Output Schema
```json
{
  "overall_status": "auto_approved | needs_human | failed",
  "prd_coverage": {
    "requirements_covered": "array of requirement IDs",
    "requirements_missing": "array of requirement IDs",
    "coverage_percent": "float"
  },
  "gate_summary": {
    "tests_passed": "boolean",
    "security_passed": "boolean",
    "quality_passed": "boolean",
    "all_files_present": "boolean"
  },
  "flags_for_human": "array of {type, description, options: [], recommended_option}",
  "auto_approval_rationale": "string — why it was auto-approved, if applicable",
  "merge_ready": "boolean"
}
```

### Auto-approval Criteria (all must be true)
- PRD coverage ≥ 95%
- All test suites passed
- Zero blocking security findings
- All required files present
- No flags raised by any agent

---

## Agent 11 — Docs & Delivery Agent

**Role**: Reads the full merged codebase and all validation reports. Generates all documentation and the client-facing proof report.

**Model**: `claude-sonnet-4-6`

**Triggered by**: Human approves the review summary

**Runs**: Once per project

### Input Schema
```json
{
  "project_id": "string",
  "project_name": "string",
  "client_name": "string",
  "prd": "PRD object",
  "architecture": "Architecture object",
  "all_task_results": "array of all task outputs",
  "all_validation_reports": "array of all test + security reports",
  "repo_url": "string"
}
```

### Output Schema
```json
{
  "documents_generated": {
    "api_docs": "path to generated API documentation",
    "setup_guide": "path to SETUP.md",
    "architecture_overview": "path to ARCHITECTURE.md",
    "runbook": "path to RUNBOOK.md",
    "env_template": "path to .env.example"
  },
  "proof_report_path": "path to branded PDF",
  "deployment_scripts": "array of file paths",
  "delivery_package_path": "path to final zip"
}
```

### Documents to Generate

**1. API Documentation** (Markdown + OpenAPI JSON)
- All endpoints with request/response examples
- Authentication instructions
- Error codes and meanings

**2. Setup Guide** (SETUP.md)
- Prerequisites
- Local development setup (step by step)
- Environment variable reference
- How to run tests

**3. Architecture Overview** (ARCHITECTURE.md)
- System diagram (ASCII)
- Component descriptions
- Data flow narrative
- Key design decisions and rationale

**4. Runbook** (RUNBOOK.md)
- How to deploy to staging
- How to deploy to production
- Common issues and fixes
- Monitoring and alerting setup

**5. Proof Report** (PDF — client-facing)
- Project summary
- What was built (plain English, feature by feature)
- Test results (N tests, N% coverage)
- Security scan results
- Performance benchmarks
- How to get started

### Behaviour Rules
- Plain English everywhere. No jargon in the proof report.
- Proof report must be readable by a non-technical client.
- Every claim in the proof report must be backed by data from the validation reports.
