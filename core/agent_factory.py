"""Craft agents — manually from a form, or auto from a natural-language brief.

The auto-craft path uses the configured LLM (if available) to synthesize a
system prompt, choose a category, pick tools and recommend a guardrail
profile. When no API key is set we still produce a reasonable agent via a
template-based fallback so the UI is always responsive.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.llm_providers import CompletionRequest, complete


# Catalog of tools the user can attach. Each tool is a stub the runtime
# will resolve later — the UI just needs id + label + description.
TOOL_CATALOG: List[Dict] = [
    {"id": "web_search",      "label": "Web search",         "desc": "Live web search via Tavily / Brave."},
    {"id": "code_interpreter","label": "Code interpreter",   "desc": "Run Python in a sandbox."},
    {"id": "browser",         "label": "Headless browser",   "desc": "Navigate and scrape pages."},
    {"id": "vector_kb",       "label": "Vector knowledge base","desc": "RAG over your docs (pgvector)."},
    {"id": "sql_query",       "label": "SQL query",          "desc": "Read-only query against your DB."},
    {"id": "email_send",      "label": "Send email",         "desc": "Resend / Postmark integration."},
    {"id": "slack_post",      "label": "Post to Slack",      "desc": "Send to a Slack channel."},
    {"id": "calendar",        "label": "Calendar",           "desc": "Read / write Google or Microsoft calendar."},
    {"id": "stripe",          "label": "Stripe",             "desc": "Create checkout sessions, refund, query."},
    {"id": "github",          "label": "GitHub",             "desc": "Open PRs, file issues, read repos."},
    {"id": "file_ops",        "label": "File operations",    "desc": "Read / write files in a sandboxed FS."},
    {"id": "image_gen",       "label": "Image generation",   "desc": "DALL-E / Stable Diffusion."},
]


CATEGORIES = [
    "sales", "support", "research", "coding", "marketing", "ops",
    "personal", "finance", "legal", "education", "creative", "other",
]


# ─────────────────────────────────────────────────────────────
# Manual builder — validates raw form input
# ─────────────────────────────────────────────────────────────

@dataclass
class AgentSpec:
    name: str
    description: str
    category: str
    system_prompt: str
    model_provider: str
    model_name: str
    temperature: float
    max_tokens: int
    tools: List[str]
    avatar_emoji: str = "🤖"
    crafted_mode: str = "manual"

    def to_db_kwargs(self) -> Dict:
        return {
            "id": str(uuid.uuid4()),
            "name": self.name,
            "slug": _slugify(self.name),
            "avatar_emoji": self.avatar_emoji,
            "description": self.description,
            "category": self.category,
            "system_prompt": self.system_prompt,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": json.dumps(self.tools),
            "crafted_mode": self.crafted_mode,
            "status": "published",
        }


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "agent"


# ─────────────────────────────────────────────────────────────
# Auto-craft — natural-language brief → AgentSpec
# ─────────────────────────────────────────────────────────────

_AUTOCRAFT_SYSTEM = """You are Agent Studio's agent-designer.
Given a user's brief, output ONLY a JSON object with this shape:

{
  "name": "short product-style name",
  "avatar_emoji": "one emoji",
  "description": "one sentence",
  "category": "one of: sales|support|research|coding|marketing|ops|personal|finance|legal|education|creative|other",
  "system_prompt": "full system prompt for the agent — be concrete about role, tone, do/don't",
  "suggested_tools": ["tool_id", ...]   // from: web_search, code_interpreter, browser, vector_kb, sql_query, email_send, slack_post, calendar, stripe, github, file_ops, image_gen
}
"""


def auto_craft(brief: str, provider: str = "anthropic", model: str = "claude-sonnet-4-6") -> AgentSpec:
    """Synthesize an AgentSpec from a natural-language description.

    Tries the configured LLM first; falls back to a deterministic template
    so we always return a usable agent even with no API key.
    """
    req = CompletionRequest(
        system=_AUTOCRAFT_SYSTEM,
        user=f"Brief:\n{brief}\n\nReturn the JSON object only.",
        model=model,
        temperature=0.4,
        max_tokens=1024,
    )
    resp = complete(req, provider)
    data = _extract_json(resp.text)

    if not data:
        data = _heuristic_spec(brief)

    return AgentSpec(
        name=data.get("name") or "Untitled Agent",
        avatar_emoji=data.get("avatar_emoji") or "🤖",
        description=data.get("description") or brief[:140],
        category=data.get("category") if data.get("category") in CATEGORIES else "other",
        system_prompt=data.get("system_prompt") or _default_prompt(brief),
        model_provider=provider,
        model_name=model,
        temperature=0.7,
        max_tokens=2048,
        tools=[t for t in (data.get("suggested_tools") or []) if t in {x["id"] for x in TOOL_CATALOG}],
        crafted_mode="auto",
    )


def _extract_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    # tolerate ```json fences
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _heuristic_spec(brief: str) -> Dict:
    """Last-resort template when no LLM is reachable."""
    low = brief.lower()
    category = "other"
    for c in CATEGORIES:
        if c in low:
            category = c
            break
    tools: List[str] = []
    if any(k in low for k in ("search", "web", "research", "news")):
        tools.append("web_search")
    if any(k in low for k in ("code", "python", "script", "compute")):
        tools.append("code_interpreter")
    if any(k in low for k in ("email", "outreach", "reply")):
        tools.append("email_send")
    if any(k in low for k in ("doc", "knowledge", "rag", "manual")):
        tools.append("vector_kb")
    if "slack" in low:
        tools.append("slack_post")
    if "calendar" in low or "meeting" in low:
        tools.append("calendar")

    name = brief.strip().split(".")[0][:50] or "Custom Agent"
    return {
        "name": name.title(),
        "avatar_emoji": "✨",
        "description": brief[:140],
        "category": category,
        "system_prompt": _default_prompt(brief),
        "suggested_tools": tools,
    }


def _default_prompt(brief: str) -> str:
    return (
        "You are an autonomous AI agent created in Agent Studio.\n\n"
        f"## Your mission\n{brief.strip()}\n\n"
        "## Operating principles\n"
        "- Be precise, useful, and honest. Cite sources when you use tools.\n"
        "- If a request violates safety policy, decline briefly and offer an alternative.\n"
        "- When the user is ambiguous, ask one clarifying question, then act.\n"
        "- Prefer concise, well-structured answers (markdown allowed).\n"
        "- Never claim capabilities you don't have; surface tool errors plainly.\n"
    )
