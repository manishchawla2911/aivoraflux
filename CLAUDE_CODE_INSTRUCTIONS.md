# Claude Code — Bootstrap Instructions
> This is the exact prompt to give Claude Code to start building the system.
> Run this from the root of your project directory.

---

## Step 1 — Initial context prompt (paste this first)

```
Read all of the following files in order before writing any code:

1. MASTER_PLAN.md
2. AGENT_SPECS.md
3. ORCHESTRATOR_SPEC.md
4. SCHEMAS.md
5. AGENT_PROMPTS.md

After reading all five documents, confirm you understand the system by summarising:
- The 11 agents and their tiers
- The 4 human touch points
- The tech stack
- The build phases in order

Do not write any code yet.
```

---

## Step 2 — Phase 1 build prompt

```
Begin Phase 1: Schemas + State

Build the following files exactly as specified in SCHEMAS.md:

1. schemas/__init__.py
2. schemas/prd.py          — ClarificationInput, ClarificationOutput, PRD and all sub-models
3. schemas/architecture.py — Architecture and all sub-models
4. schemas/task.py         — Task, TaskGraph, BuilderOutput and all sub-models
5. schemas/validation_report.py — TestReport, SecurityQualityReport and all sub-models
6. schemas/delivery.py     — ReviewSummary, DeliveryPackage and all sub-models

Then build:
7. core/state.py — SQLite models using SQLModel that mirror the database schema in ORCHESTRATOR_SPEC.md section 6

Then write tests:
8. tests/test_schemas.py   — Test serialisation round-trips for every schema
9. tests/test_state.py     — Test SQLite create/read/update for every model

Requirements:
- Use Pydantic v2
- Use SQLModel for the ORM
- All schemas must have model_config = ConfigDict(extra="forbid") 
- All tests must pass before marking Phase 1 complete
- Run: pytest tests/ -v and confirm all green
```

---

## Step 3 — Phase 2 build prompt

```
Begin Phase 2: Base Agent + Orchestrator skeleton

Build in this order:

1. agents/__init__.py
2. agents/base_agent.py
   - Abstract BaseAgent class
   - Properties: name, model, max_retries
   - Methods: run(input) -> AgentResult [abstract], run_with_retry(input) -> AgentResult
   - AgentResult dataclass: status, payload, error, duration_ms
   - Retry logic: 3 attempts, exponential backoff (2s, 8s, 32s)
   - Logging on every attempt

3. core/event_bus.py
   - Simple asyncio pub/sub
   - Methods: subscribe(event, handler), publish(event, data), unsubscribe(event, handler)
   - Events: agent.completed, agent.failed, agent.flag_human, task.ready, validation.complete, project.phase_done

4. core/task_graph.py
   - TaskGraph class wrapping the TaskGraph schema
   - Methods: get_ready_tasks() -> list[Task] (tasks whose deps are all done)
   - Methods: mark_done(task_id), mark_failed(task_id), is_complete() -> bool, get_batches() -> list[list[Task]]

5. core/orchestrator.py
   - Orchestrator class
   - load_project(project_id), run_project(project_id)
   - Internal loop: poll for ready tasks, dispatch agents, handle results
   - MAX_PARALLEL_AGENTS = 4 (from env)
   - State transitions matching the state machine in ORCHESTRATOR_SPEC.md section 2
   - Event subscriptions for all events in event_bus

6. core/notifier.py
   - SlackNotifier class
   - send_decision_request(project_id, decision_type, context_url)
   - send_reminder(project_id, decision_id)
   - Uses SLACK_WEBHOOK_URL from env

Tests:
7. tests/test_task_graph.py — Test ready task detection, dependency resolution, batch generation
8. tests/test_orchestrator.py — Test state transitions with a mock project and mock agents
```

---

## Step 4 — Phase 3 build prompt

```
Begin Phase 3: Thinking Agents

Build the three thinking agents. Each agent:
- Subclasses BaseAgent
- Loads its system prompt from prompts/{agent_name}.md at init
- Calls the Anthropic API using the model specified in AGENT_SPECS.md
- Validates its output against the corresponding Pydantic schema
- Re-prompts once if output fails schema validation (inject the validation error)

Agents to build:

1. agents/clarification_agent.py
   - Model: claude-sonnet-4-6
   - Input: ClarificationInput schema
   - Output: ClarificationOutput schema
   - Conversation loop: supports multiple rounds (passes previous_answers back)

2. agents/architect_agent.py
   - Model: claude-opus-4-5 with extended thinking (budget_tokens=8000)
   - Input: PRD + budget_usd + timeline_weeks + deployment_target
   - Output: Architecture schema
   - Must include all API contracts, full DB schema, full folder structure

3. agents/task_planner_agent.py
   - Model: claude-sonnet-4-6
   - Input: Architecture + PRD
   - Output: TaskGraph schema
   - Post-processing: validate that no two parallel tasks share a file

Also build:
4. prompts/ directory — copy each prompt from AGENT_PROMPTS.md into its own .md file

Tests:
5. tests/test_thinking_agents.py
   - Mock the Anthropic API
   - Test each agent with a sample input fixture
   - Assert output validates against its schema
   - Test retry behaviour on invalid JSON response
   - Test clarification agent multi-round conversation
```

---

## Step 5 — Phase 4 build prompt

```
Begin Phase 4: Builder Agents

All four builder agents follow the same pattern:
- Subclass BaseAgent
- Build the CLAUDE.md context file for the task (inject task context into the template from AGENT_SPECS.md)
- Spawn a Claude Code subprocess with that context
- Parse the JSON summary from Claude Code's output
- Validate against BuilderOutput schema
- Create a git branch, commit the files, push the branch
- Return BuilderOutput

Agents to build:

1. agents/backend_agent.py
   - CLAUDE.md template: from AGENT_SPECS.md section "Agent 4 — Backend Agent"
   - Git branch pattern: agent/backend/{task_id}

2. agents/frontend_agent.py
   - CLAUDE.md template: from AGENT_SPECS.md section "Agent 5 — Frontend Agent"
   - Git branch pattern: agent/frontend/{task_id}

3. agents/integration_agent.py
   - CLAUDE.md template: from AGENT_SPECS.md section "Agent 6 — Integration Agent"
   - Also parses env_vars_required from output
   - Git branch pattern: agent/integration/{task_id}

4. agents/devops_agent.py
   - CLAUDE.md template: from AGENT_SPECS.md section "Agent 7 — DevOps Agent"
   - Git branch pattern: agent/devops/{task_id}

Shared utility to build:
5. core/git_manager.py
   - create_branch(branch_name), commit_files(files, message), push_branch(), create_pr(title, body)
   - Uses GitHub API via GITHUB_TOKEN

Tests:
6. tests/test_builder_agents.py
   - Mock Claude Code subprocess
   - Test CLAUDE.md generation for each agent type
   - Test git operations with a test repo
```

---

## Step 6 — Phase 5 build prompt

```
Begin Phase 5: Validation Agents

1. agents/test_writer_agent.py
   - Model: claude-sonnet-4-6 with tool use
   - Tools: read_file, write_file (test files only), run_command
   - Input: task_id, task_spec, files_created, existing_tests
   - Output: TestReport schema
   - Runs in the same branch as the builder task
   - After writing tests, runs the test suite and captures results
   - Parses test runner output (pytest JSON output or vitest --reporter=json)
   - Attempt one fix if tests fail — then report, do not loop

2. agents/security_quality_agent.py
   - Model: claude-sonnet-4-6 with tool use
   - Tools: run_command, read_file
   - Input: task_id, files_created, agent_type
   - Output: SecurityQualityReport schema
   - Runs Semgrep for security, Bandit for Python / ESLint for JS
   - Runs custom regex for secret patterns (see patterns below)
   - Computes scores and identifies blocking findings

Secret scan patterns to implement:
   - AWS keys: AKIA[0-9A-Z]{16}
   - Generic API keys: [aA][pP][iI]_?[kK][eE][yY].*['"][0-9a-zA-Z]{32,}['"]
   - Passwords in code: password\s*=\s*['"][^'"]{8,}['"]
   - Private keys: -----BEGIN (RSA |EC )?PRIVATE KEY-----
   - JWT secrets: jwt.*secret.*=.*['"][^'"]{16,}['"]

Tests:
3. tests/test_validation_agents.py
   - Test on a known-good Python file (should pass all gates)
   - Test on a known-bad Python file (hardcoded password, high complexity)
   - Assert correct findings are detected
   - Assert scores are computed correctly
```

---

## Step 7 — Phase 6 build prompt

```
Begin Phase 6: Review Agent + Docs & Delivery Agent

1. agents/review_agent.py
   - Model: claude-opus-4-5
   - Input: project_id, prd, architecture, all task_results, all_files
   - Output: ReviewSummary schema
   - Checks PRD coverage — maps every requirement to tasks that implement it
   - Aggregates all gate results
   - Identifies flags for human
   - Auto-approves if all criteria met (from AGENT_SPECS.md section "Agent 10")

2. agents/docs_delivery_agent.py
   - Model: claude-sonnet-4-6
   - Input: full project context + all validation reports
   - Output: DeliveryPackage schema
   - Generates: API docs (Markdown), SETUP.md, ARCHITECTURE.md, RUNBOOK.md, .env.example
   - Generates proof report PDF using WeasyPrint + Jinja2 template
   - Packages everything into a zip at projects/{project_id}/delivery/

3. templates/proof_report.html
   - Professional PDF template
   - Sections: Cover, What Was Built, Quality Evidence, How to Get Started
   - Inject: project_name, client_name, features list, test results, security scan summary

4. templates/review_summary.md
   - Internal template for human review notification
   - Shows: coverage %, gate results, flags requiring decision (with options)

End-to-end test:
5. tests/test_end_to_end.py
   - Run the full pipeline on a minimal sample project
   - Input: a simple PRD for a todo list API (no frontend, no integrations)
   - Assert: task graph created, all agents run, validation passes, delivery package generated
   - Use mocked Anthropic API responses and mocked Claude Code output
```

---

## Step 8 — Phase 7 build prompt

```
Begin Phase 7: Dashboard + Final wiring

1. dashboard/main.py — FastAPI app with these routes:
   GET  /                              → project list
   GET  /projects/{id}                → project detail + current state
   GET  /projects/{id}/decisions/{did} → decision review UI (human touch point)
   POST /projects/{id}/decisions/{did} → submit decision
   POST /projects/new                  → create new project (intake form)
   GET  /projects/{id}/logs            → live log stream (SSE)

2. dashboard/templates/
   - index.html   — project list, status badges
   - project.html — task graph view, current phase, log tail
   - decision.html — shows decision context, options, submit button

3. Wire everything into main entry point:
   - main.py at root: starts FastAPI + orchestrator loop together
   - Orchestrator runs in background asyncio task
   - Dashboard serves human touch points

4. requirements.txt — pin all dependencies with exact versions

5. README.md — How to install, configure, and run the system. 
   Include: setup steps, env var reference, how to create a first project, 
   how to respond to a decision notification.

Final check:
6. Run the full end-to-end test from Phase 6 against the real FastAPI server
7. Confirm all 4 human touch points work via the dashboard UI
8. Confirm Slack notification fires on a flagged decision
```
