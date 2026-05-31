# Orchestrator Specification
> Describes how agents are dispatched, how state is tracked, and how the system handles failures.

---

## 1. Orchestrator Responsibilities

The orchestrator is the runtime engine. It does not make decisions — it moves work through the pipeline. Decisions are made by agents or escalated to humans.

**What it does:**
- Monitors project state in SQLite
- Dispatches agents when their trigger conditions are met
- Tracks task graph progress (which tasks are pending, running, done, failed)
- Routes agent outputs to the next agent
- Sends human notifications for decision points
- Handles retries and escalation

**What it does NOT do:**
- Read or understand code
- Make architectural decisions
- Communicate directly with clients

---

## 2. State Machine — Project Lifecycle

```
CREATED
  │ intake form submitted
  ▼
CLARIFYING
  │ PRD draft ready
  ▼
AWAITING_PRD_APPROVAL        ◄── Human touch point #1
  │ human approves
  ▼
ARCHITECTING
  │ architecture doc ready
  ▼
AWAITING_ARCH_APPROVAL       ◄── Human touch point #2
  │ human approves
  ▼
PLANNING
  │ task graph ready
  ▼
BUILDING                     (parallel tasks running)
  │ all tasks complete
  ▼
VALIDATING                   (parallel validation per task)
  │ all validation complete
  ▼
REVIEWING
  │ auto-approved OR
  ▼
AWAITING_REVIEW_APPROVAL     ◄── Human touch point #3 (if flags exist)
  │ approved
  ▼
GENERATING_DOCS
  │ docs complete
  ▼
AWAITING_DELIVERY_SIGNOFF    ◄── Human touch point #4
  │ approved
  ▼
DELIVERED

(Any state can transition to FAILED or PAUSED)
```

---

## 3. Task Graph Execution

### DAG Structure
```python
# Each task knows its dependencies
tasks = [
    Task(id="BE-001", depends_on=[]),
    Task(id="BE-002", depends_on=["BE-001"]),
    Task(id="FE-001", depends_on=[]),          # runs parallel to BE-001
    Task(id="FE-002", depends_on=["BE-001"]),  # waits for BE-001 API contract
    Task(id="INT-001", depends_on=["BE-002"]),
    Task(id="OPS-001", depends_on=[]),          # runs parallel — no app logic dependency
]
```

### Execution Loop (pseudocode)
```python
async def run_task_graph(project_id):
    while not all_tasks_complete(project_id):
        ready_tasks = get_ready_tasks(project_id)
        # ready = dependencies done, not yet started, slots available

        for task in ready_tasks[:MAX_PARALLEL_AGENTS]:
            asyncio.create_task(run_task(project_id, task))

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

### Task Status Transitions
```
PENDING → RUNNING → VALIDATING → DONE
                  ↘ FAILED (after retries) → ESCALATED
```

---

## 4. Agent Dispatch Interface

Every agent must implement `BaseAgent`:

```python
class BaseAgent:
    name: str
    model: str
    max_retries: int = 3

    async def run(self, input: dict) -> AgentResult:
        """
        Implement this in each agent subclass.
        Must return AgentResult with status and payload.
        """
        raise NotImplementedError

    async def run_with_retry(self, input: dict) -> AgentResult:
        """Called by orchestrator. Wraps run() with retry logic."""
        for attempt in range(self.max_retries):
            try:
                result = await self.run(input)
                if result.status != "failed":
                    return result
            except Exception as e:
                log_error(self.name, attempt, e)
                await asyncio.sleep(2 ** attempt * 2)
        return AgentResult(status="failed", error="Max retries exceeded")
```

---

## 5. Human Notification Protocol

When the orchestrator needs a human decision, it:
1. Pauses the relevant project (other projects continue running)
2. Saves the decision context to the database
3. Sends a Slack message with a direct link to the decision UI
4. Polls every 30 minutes — sends a reminder if no response after 4 hours
5. After 24 hours with no response — marks project as PAUSED

### Slack message format
```
🔔 *[Project: {project_name}]* needs your input

*Type*: {PRD Approval | Architecture Approval | Flagged Decision | Delivery Sign-off}
*Summary*: {one-line description}

→ Review & decide: {dashboard_url}/projects/{project_id}/decisions/{decision_id}
```

---

## 6. Database Schema (SQLite via SQLModel)

```sql
-- Projects
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    client_name TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Tasks
CREATE TABLE task (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES project(id),
    title TEXT,
    agent_type TEXT,
    status TEXT,
    depends_on TEXT,        -- JSON array of task IDs
    context_package TEXT,   -- JSON
    result TEXT,            -- JSON agent output
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0
);

-- Agent messages (full audit trail)
CREATE TABLE agent_message (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    from_agent TEXT,
    to_agent TEXT,
    type TEXT,
    payload TEXT,           -- JSON
    timestamp TIMESTAMP
);

-- Human decisions
CREATE TABLE human_decision (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    decision_type TEXT,
    context TEXT,           -- JSON — what the human needs to see
    options TEXT,           -- JSON array
    chosen_option TEXT,
    decided_at TIMESTAMP,
    notified_at TIMESTAMP,
    reminder_count INTEGER DEFAULT 0
);

-- Validation reports
CREATE TABLE validation_report (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    report_type TEXT,       -- test | security_quality
    report TEXT,            -- JSON
    status TEXT,
    created_at TIMESTAMP
);
```

---

## 7. Event Bus

Internal pub/sub for agent communication within a project run.

```python
# Events the orchestrator subscribes to:
EVENTS = {
    "agent.completed":     handle_agent_completed,
    "agent.failed":        handle_agent_failed,
    "agent.flag_human":    handle_flag_human,
    "task.ready":          dispatch_task,
    "validation.complete": handle_validation_complete,
    "project.phase_done":  advance_project_phase,
}
```

---

## 8. Parallel Execution Constraints

| Constraint | Value | Reason |
|---|---|---|
| Max parallel agents | 4 | Anthropic API rate limits |
| Max parallel tasks per project | 2 | Keep resource footprint manageable |
| Claude Code subprocess timeout | 10 min | Kill runaway agents |
| API agent timeout | 3 min | Prevent hung requests |
| Max active projects | 3 | Human can only review so many in parallel |

---

## 9. Logging

Every agent action is logged to a structured log file per project:

```
projects/{project_id}/logs/
  orchestrator.log
  {agent_name}.log
  validation.log
```

Log format:
```json
{
  "timestamp": "ISO8601",
  "project_id": "uuid",
  "task_id": "uuid | null",
  "agent": "string",
  "event": "string",
  "duration_ms": "integer",
  "status": "string",
  "detail": "string"
}
```
