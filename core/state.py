"""SQLite state store using SQLModel.

Mirrors the schema in ORCHESTRATOR_SPEC.md section 6.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine


def utcnow() -> datetime:
    """Naive UTC timestamp without the deprecated ``datetime.utcnow()``.

    Returns a naive datetime (tzinfo stripped) so it stays byte-compatible
    with rows written before this change and with SQLite's string ordering.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Backwards-compatible alias used as a SQLModel default_factory.
_utcnow = utcnow


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: str = Field(primary_key=True)
    name: str
    client_name: Optional[str] = None
    status: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Task(SQLModel, table=True):
    __tablename__ = "task"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    title: Optional[str] = None
    agent_type: Optional[str] = None
    status: Optional[str] = None
    depends_on: Optional[str] = None  # JSON string of task IDs
    context_package: Optional[str] = None  # JSON
    result: Optional[str] = None  # JSON of agent output
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0


class AgentMessage(SQLModel, table=True):
    __tablename__ = "agent_message"

    id: str = Field(primary_key=True)
    project_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    from_agent: Optional[str] = None
    to_agent: Optional[str] = None
    type: Optional[str] = None
    payload: Optional[str] = None  # JSON
    timestamp: datetime = Field(default_factory=_utcnow)


class HumanDecision(SQLModel, table=True):
    __tablename__ = "human_decision"

    id: str = Field(primary_key=True)
    project_id: Optional[str] = Field(default=None, index=True)
    decision_type: Optional[str] = None
    context: Optional[str] = None  # JSON
    options: Optional[str] = None  # JSON
    chosen_option: Optional[str] = None
    decided_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None
    reminder_count: int = 0


class ValidationReport(SQLModel, table=True):
    __tablename__ = "validation_report"

    id: str = Field(primary_key=True)
    project_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    report_type: Optional[str] = None  # test | security_quality
    report: Optional[str] = None  # JSON
    status: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


# ─────────────────────────────────────────────────────────────
# Agent Studio product tables — agents the user can craft & run.
# ─────────────────────────────────────────────────────────────

class Agent(SQLModel, table=True):
    """A user-crafted AI agent: name, prompt, tools, provider, guardrails."""
    __tablename__ = "agent"

    id: str = Field(primary_key=True)
    owner_email: Optional[str] = Field(default=None, index=True)
    name: str
    slug: Optional[str] = Field(default=None, index=True)
    avatar_emoji: str = "🤖"
    description: Optional[str] = None
    category: Optional[str] = None  # sales, support, research, coding, ops, …

    # Core behavior
    system_prompt: str
    model_provider: str = "anthropic"         # anthropic | openai | google | mistral | ollama | …
    model_name: str = "claude-sonnet-4-6"
    temperature: float = 0.7
    max_tokens: int = 2048

    # JSON-encoded fields
    tools: Optional[str] = None               # JSON list of tool ids
    knowledge_sources: Optional[str] = None   # JSON list of URLs / docs
    guardrail_profile_id: Optional[str] = Field(default=None, foreign_key="guardrail_profile.id")
    metadata_json: Optional[str] = None       # JSON freeform

    crafted_mode: str = "manual"              # manual | auto
    status: str = "draft"                     # draft | published | archived
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentRun(SQLModel, table=True):
    """A single execution of an agent — useful for analytics & audit logs."""
    __tablename__ = "agent_run"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agent.id", index=True)
    input_text: str
    output_text: Optional[str] = None
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    guardrail_verdict: Optional[str] = None   # passed | blocked | flagged
    guardrail_reason: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class ProviderConfig(SQLModel, table=True):
    """User-configured LLM provider credentials & defaults."""
    __tablename__ = "provider_config"

    id: str = Field(primary_key=True)
    owner_email: Optional[str] = Field(default=None, index=True)
    provider: str                              # anthropic | openai | google | mistral | ollama | …
    display_name: Optional[str] = None
    api_key_env: Optional[str] = None          # name of the env var holding the key
    base_url: Optional[str] = None             # for local / self-hosted endpoints
    default_model: Optional[str] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class GuardrailProfile(SQLModel, table=True):
    """A reusable bundle of safety configuration applied to one or more agents."""
    __tablename__ = "guardrail_profile"

    id: str = Field(primary_key=True)
    owner_email: Optional[str] = Field(default=None, index=True)
    name: str
    description: Optional[str] = None

    # Toggles
    nemo_guardrails: bool = True               # NVIDIA NeMo Guardrails dialog policy
    llama_guard: bool = True                   # Meta Llama Guard classifier
    prompt_injection_detection: bool = True    # heuristic + classifier
    pii_redaction: bool = True                 # email / phone / SSN scrub
    toxicity_filter: bool = True
    jailbreak_shield: bool = True
    output_moderation: bool = True             # OpenAI / Anthropic moderation API

    blocked_topics: Optional[str] = None       # JSON list
    allowed_domains: Optional[str] = None      # JSON list
    rate_limit_per_min: int = 60
    max_output_chars: int = 16000

    created_at: datetime = Field(default_factory=_utcnow)


# ─────────────────────────────────────────────────────────────
# Workspace layer — an "AI company": a roster of role agents that
# share a common memory. Members reuse the existing Agent row.
# ─────────────────────────────────────────────────────────────

class Workspace(SQLModel, table=True):
    """An owner's AI company — a named container for a roster + shared memory."""
    __tablename__ = "workspace"

    id: str = Field(primary_key=True)
    owner_email: Optional[str] = Field(default=None, index=True)
    name: str
    company_description: Optional[str] = None
    mission: Optional[str] = None
    status: str = "active"                      # active | archived
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkspaceMember(SQLModel, table=True):
    """Join row: places an existing Agent into a workspace under a role."""
    __tablename__ = "workspace_member"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    agent_id: str = Field(foreign_key="agent.id", index=True)
    role: str                                   # catalog role id, e.g. "ceo"
    display_name: Optional[str] = None
    order_index: int = 0
    parent_member_id: Optional[str] = None     # member that spawned this one
    origin: str = "seed"                        # seed | spawned
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceMemory(SQLModel, table=True):
    """One shared-memory entry. Canonical here; mirrored into ChromaDB by id."""
    __tablename__ = "workspace_memory"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    author: Optional[str] = None                # member display name or "owner"
    kind: str = "note"                          # goal | decision | fact | note
    content: str = ""
    tags: Optional[str] = None                  # JSON list
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceChatMessage(SQLModel, table=True):
    """One message in a workspace's single internal chat channel."""
    __tablename__ = "workspace_chat_message"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    author_kind: str = "owner"                  # owner | agent
    author_member_id: Optional[str] = None      # WorkspaceMember.id when agent-authored
    author_name: str = "Owner"                  # display label in the transcript
    content: str = ""
    mentions: Optional[str] = None              # JSON list of resolved member ids
    triggered_by_id: Optional[str] = None       # message id that caused this one
    pinned_memory_id: Optional[str] = None      # WorkspaceMemory.id once pinned
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceProject(SQLModel, table=True):
    """A unit of work in a workspace; anchors a spawned team + a cost quote."""
    __tablename__ = "workspace_project"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    brief: str = ""
    client_name: Optional[str] = None
    status: str = "estimating"                  # estimating | staffed | archived
    pm_member_id: Optional[str] = None          # spawned PM's WorkspaceMember.id
    estimate_json: Optional[str] = None         # JSON CostEstimate (client quote)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkspaceChannel(SQLModel, table=True):
    """Links a workspace to an external chat platform channel (subsystem E)."""
    __tablename__ = "workspace_channel"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    platform: str                               # telegram | slack | whatsapp
    external_id: str = Field(index=True)        # chat / channel / phone id
    label: Optional[str] = None
    token_env: Optional[str] = None             # env var name holding the bot token
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


# ─────────────────────────────────────────────────────────────
# Observability — append-only telemetry for the agent fleet & studio.
# ─────────────────────────────────────────────────────────────

class AgentEvent(SQLModel, table=True):
    """One telemetry record per meaningful agent moment.

    Captures a terminal run, a failed attempt, an escalation, or a guardrail
    block — for BOTH the fleet pipeline agents and Agent Studio runs. Stores
    sizes/counts/metadata only, never raw input/output text, so telemetry can
    never re-introduce the PII/secret leakage that guardrails exist to prevent.
    """
    __tablename__ = "agent_event"

    id: str = Field(primary_key=True)
    ts: datetime = Field(default_factory=_utcnow, index=True)
    source: str = "fleet"                          # fleet | studio
    project_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    agent_id: Optional[str] = None                 # studio Agent.id, when applicable
    agent_name: Optional[str] = Field(default=None, index=True)
    agent_type: Optional[str] = None               # backend | frontend | …
    event_type: str = "completed"                  # attempt|completed|failed|escalated|guardrail_block|run
    status: Optional[str] = None                   # success|failed|needs_human|blocked|passed
    attempt: int = 0
    retry_count: int = 0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    guardrail_verdict: Optional[str] = None
    error: Optional[str] = None                    # truncated
    input_chars: int = 0
    output_chars: int = 0
    meta_json: Optional[str] = None                # small JSON blob


_engine = None


def get_engine(database_url: Optional[str] = None):
    """Return the singleton engine. Pass database_url to override."""
    global _engine
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./agent_fleet.db")
    if _engine is None or database_url is not None:
        if _engine is not None:
            # Re-pointing the engine (e.g. tests overriding the DB URL): dispose
            # the previous one so its connection pool is released.
            _engine.dispose()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db(database_url: Optional[str] = None) -> None:
    """Create all tables. Idempotent."""
    engine = get_engine(database_url)
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Open a new session bound to the current engine."""
    return Session(get_engine())
