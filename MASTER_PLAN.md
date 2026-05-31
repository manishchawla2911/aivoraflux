# Autonomous Agent Fleet — Master Plan
> Provide this document to Claude Code first. It is the root context for the entire system.

---

## 1. What We Are Building

An autonomous software delivery pipeline that takes a client brief as input and produces a fully tested, documented, deployed codebase as output — with minimal human intervention.

The system is operated by a single orchestrator (you). You approve specs and resolve flagged decisions. All code generation, testing, security scanning, and documentation is handled by specialized AI agents.

---

## 2. System Architecture Overview

```
CLIENT BRIEF
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  INTAKE LAYER                                        │
│  Structured form → Clarification Agent → PRD Doc    │
└─────────────────────────────────────────────────────┘
     │ (human approves PRD)
     ▼
┌─────────────────────────────────────────────────────┐
│  THINKING LAYER  (runs once per project)             │
│  Architect Agent → Task Planner Agent                │
└─────────────────────────────────────────────────────┘
     │ (human approves architecture)
     ▼
┌─────────────────────────────────────────────────────┐
│  BUILD LAYER  (parallel, per task)                   │
│  Backend Agent │ Frontend Agent │ Integration Agent  │
│  DevOps Agent                                        │
└─────────────────────────────────────────────────────┘
     │ (after each task, automatically)
     ▼
┌─────────────────────────────────────────────────────┐
│  VALIDATION LAYER  (per task, automated)             │
│  Test Writer Agent │ Security & Quality Agent        │
└─────────────────────────────────────────────────────┘
     │ (all tasks complete)
     ▼
┌─────────────────────────────────────────────────────┐
│  REVIEW LAYER  (surfaces only decisions for human)   │
│  Review Agent → Human approval → Merge               │
└─────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  DELIVERY LAYER  (automated)                         │
│  Docs & Delivery Agent → Client Package              │
└─────────────────────────────────────────────────────┘
```

---

## 3. Tech Stack Decisions

### Orchestration Layer
- **Language**: Python 3.11+
- **State store**: SQLite (via SQLModel) — simple, zero-dependency, portable
- **Task queue**: Python asyncio + asyncio.Queue for parallel agent dispatch
- **API**: FastAPI — exposes webhook endpoints and a minimal dashboard
- **Schema validation**: Pydantic v2

### Agent Runtime
- **Thinking agents** (Architect, Task Planner, Review, Docs): Anthropic Claude API directly — `claude-opus-4-5` for deep reasoning tasks
- **Builder agents** (Backend, Frontend, Integration, DevOps): Claude Code CLI — runs in subprocess per task with injected context
- **Validation agents** (Test Writer, Security): Claude API + tool calls to run shell commands

### Code & Version Control
- **Git**: Every agent works in its own branch. Branches follow pattern: `agent/{agent-type}/{task-id}`
- **GitHub Actions**: Triggered by agent PR creation for CI gates
- **PR format**: Structured — machine-readable frontmatter + human-readable summary

### Integrations
- **Notifications**: Slack webhook (flag human for decision)
- **Client delivery**: PDF generation via WeasyPrint + Jinja2 templates
- **Secrets**: python-dotenv + `.env` per project, never committed

---

## 4. Project Directory Structure

```
agent-fleet/
│
├── core/                        # Orchestration engine
│   ├── orchestrator.py          # Main loop — dispatches agents, tracks state
│   ├── task_graph.py            # Dependency-aware task graph (DAG)
│   ├── state.py                 # SQLite state management (SQLModel)
│   ├── event_bus.py             # Pub/sub for agent communication
│   └── notifier.py              # Slack / email notifications to human
│
├── agents/                      # One file per agent
│   ├── base_agent.py            # Abstract base — input/output contract, retry logic
│   ├── clarification_agent.py
│   ├── architect_agent.py
│   ├── task_planner_agent.py
│   ├── backend_agent.py
│   ├── frontend_agent.py
│   ├── integration_agent.py
│   ├── devops_agent.py
│   ├── test_writer_agent.py
│   ├── security_quality_agent.py
│   ├── review_agent.py
│   └── docs_delivery_agent.py
│
├── prompts/                     # System prompts — one .md per agent
│   ├── clarification_agent.md
│   ├── architect_agent.md
│   ├── task_planner_agent.md
│   ├── backend_agent.md
│   ├── frontend_agent.md
│   ├── integration_agent.md
│   ├── devops_agent.md
│   ├── test_writer_agent.md
│   ├── security_quality_agent.md
│   ├── review_agent.md
│   └── docs_delivery_agent.md
│
├── schemas/                     # Pydantic models — agent I/O contracts
│   ├── prd.py                   # PRD schema
│   ├── architecture.py          # Architecture doc schema
│   ├── task.py                  # Task + task graph schema
│   ├── validation_report.py     # Test/security report schema
│   └── delivery.py              # Final delivery package schema
│
├── templates/                   # Jinja2 templates
│   ├── proof_report.html        # Client-facing PDF delivery report
│   └── review_summary.md        # Internal review summary for human
│
├── projects/                    # Runtime — one folder per active project
│   └── {project_id}/
│       ├── prd.json
│       ├── architecture.json
│       ├── task_graph.json
│       ├── validation_reports/
│       └── delivery/
│
├── dashboard/                   # Minimal FastAPI web UI
│   ├── main.py
│   └── templates/
│
├── tests/                       # Tests for the fleet system itself
├── .env.example
├── requirements.txt
└── README.md
```

---

## 5. Agent Communication Protocol

All agents communicate via structured JSON. No agent reads another agent's source code or internal state directly.

### Message envelope (all inter-agent messages use this)
```json
{
  "message_id": "uuid",
  "project_id": "uuid",
  "task_id": "uuid | null",
  "from_agent": "architect",
  "to_agent": "task_planner",
  "type": "output | error | flag_human",
  "payload": { ... },
  "timestamp": "ISO8601",
  "retry_count": 0
}
```

### Agent result statuses
- `success` — output produced, next agent can proceed
- `needs_human` — agent flagged a decision it cannot make alone
- `failed` — agent errored after max retries, human must intervene
- `blocked` — waiting on a dependency task to complete

---

## 6. Human Touch Points

The human (you) interacts with the system in exactly four ways:

| Touch point | When | Time required |
|---|---|---|
| **PRD approval** | After Clarification Agent | 5–10 min |
| **Architecture approval** | After Architect Agent | 5–10 min |
| **Flagged decision** | When Review Agent raises a flag | 2–5 min per flag |
| **Final delivery sign-off** | Before client handoff | 5 min |

Everything else is automated. If the system pings you for anything outside these four, that is a bug in the system.

---

## 7. Error Handling & Retry Policy

| Scenario | Behaviour |
|---|---|
| Agent API call fails | Retry 3× with exponential backoff (2s, 8s, 32s) |
| Agent output fails schema validation | Re-prompt once with validation error injected |
| Validation gate fails | Builder agent gets failure report, re-runs task once |
| Second validation failure | Escalate to human via Slack |
| Human does not respond within 24h | Pause project, send reminder |

---

## 8. Build Order for Claude Code

Build the system in this exact sequence. Each phase is independently testable.

```
Phase 1: Schemas + State
  → schemas/prd.py, architecture.py, task.py, validation_report.py, delivery.py
  → core/state.py (SQLite models)
  → Test: schema serialisation round-trips

Phase 2: Base Agent + Orchestrator skeleton
  → agents/base_agent.py
  → core/orchestrator.py (skeleton — no agents plugged in yet)
  → core/task_graph.py
  → core/event_bus.py
  → Test: orchestrator starts, handles empty project

Phase 3: Thinking agents
  → agents/clarification_agent.py
  → agents/architect_agent.py
  → agents/task_planner_agent.py
  → Test: feed a sample PRD, verify task graph output

Phase 4: Builder agents
  → agents/backend_agent.py
  → agents/frontend_agent.py
  → agents/integration_agent.py
  → agents/devops_agent.py
  → Test: single task execution per agent type

Phase 5: Validation agents
  → agents/test_writer_agent.py
  → agents/security_quality_agent.py
  → Test: validation runs on a known-good and known-bad codebase

Phase 6: Review + Delivery
  → agents/review_agent.py
  → agents/docs_delivery_agent.py
  → templates/proof_report.html
  → Test: full pipeline end-to-end on a small project

Phase 7: Dashboard + Notifier
  → dashboard/main.py
  → core/notifier.py
  → Test: Slack notification on flag, dashboard shows project state
```

---

## 9. Environment Variables Required

```bash
# Anthropic
ANTHROPIC_API_KEY=

# GitHub
GITHUB_TOKEN=
GITHUB_ORG=
GITHUB_DEFAULT_REPO_PREFIX=

# Notifications
SLACK_WEBHOOK_URL=
NOTIFICATION_EMAIL=

# System
DATABASE_URL=sqlite:///./agent_fleet.db
PROJECTS_BASE_PATH=./projects
LOG_LEVEL=INFO
MAX_PARALLEL_AGENTS=4
AGENT_RETRY_MAX=3
```
