"""Pluggable LLM provider registry.

Lets a crafted Agent execute against any supported backend — cloud-hosted
(Anthropic, OpenAI, Google, Mistral, Groq, Together) or local (Ollama,
vLLM, LM Studio). Each provider implements a uniform `complete(...)`.

Real network calls are guarded — if the provider SDK or API key is missing
we fall back to a deterministic stub so the platform still demos cleanly.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompletionRequest:
    system: str
    user: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 2048
    history: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class CompletionResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    stubbed: bool = False


# ─────────────────────────────────────────────────────────────
# Provider catalog — what the UI offers + price hints for the
# cost estimator. Pricing in USD per million tokens (rough).
# ─────────────────────────────────────────────────────────────

PROVIDER_CATALOG: Dict[str, Dict] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "kind": "cloud",
        "env": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-opus-4-7", "name": "Claude Opus 4.7", "in": 15.0, "out": 75.0},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "in": 3.0, "out": 15.0},
            {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "in": 0.8, "out": 4.0},
        ],
    },
    "openai": {
        "label": "OpenAI GPT",
        "kind": "cloud",
        "env": "OPENAI_API_KEY",
        "models": [
            {"id": "gpt-5", "name": "GPT-5", "in": 5.0, "out": 15.0},
            {"id": "gpt-4o", "name": "GPT-4o", "in": 2.5, "out": 10.0},
            {"id": "gpt-4o-mini", "name": "GPT-4o mini", "in": 0.15, "out": 0.6},
        ],
    },
    "google": {
        "label": "Google Gemini",
        "kind": "cloud",
        "env": "GOOGLE_API_KEY",
        "models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "in": 1.25, "out": 5.0},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "in": 0.075, "out": 0.3},
        ],
    },
    "mistral": {
        "label": "Mistral",
        "kind": "cloud",
        "env": "MISTRAL_API_KEY",
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large", "in": 2.0, "out": 6.0},
            {"id": "mistral-small-latest", "name": "Mistral Small", "in": 0.2, "out": 0.6},
        ],
    },
    "groq": {
        "label": "Groq (ultra-fast)",
        "kind": "cloud",
        "env": "GROQ_API_KEY",
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "in": 0.59, "out": 0.79},
        ],
    },
    "ollama": {
        "label": "Ollama (local)",
        "kind": "local",
        "env": None,
        "base_url_default": "http://localhost:11434",
        # Fallback list — overridden at runtime by discover_ollama_models()
        # when an Ollama daemon is reachable. Includes common small/medium
        # models so the demo still works on a fresh machine.
        "models": [
            {"id": "gemma3:1b",   "name": "Gemma 3 1B (local)",    "in": 0.0, "out": 0.0},
            {"id": "llama3.2:3b", "name": "Llama 3.2 3B (local)",  "in": 0.0, "out": 0.0},
            {"id": "phi3:mini",   "name": "Phi-3 Mini (local)",    "in": 0.0, "out": 0.0},
            {"id": "qwen2.5:7b",  "name": "Qwen2.5 7B (local)",    "in": 0.0, "out": 0.0},
            {"id": "mistral:7b",  "name": "Mistral 7B (local)",    "in": 0.0, "out": 0.0},
        ],
    },
    "lmstudio": {
        "label": "LM Studio (local)",
        "kind": "local",
        "env": None,
        "base_url_default": "http://localhost:1234/v1",
        "models": [
            {"id": "local-model", "name": "Loaded local model", "in": 0.0, "out": 0.0},
        ],
    },
}


def discover_ollama_models(base_url: Optional[str] = None, timeout: float = 1.5) -> List[Dict]:
    """Live-query the Ollama daemon for installed models.

    Falls back silently to an empty list when the daemon isn't reachable —
    callers should keep their static catalog as a backup.
    """
    base = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import json as _json
        import urllib.request
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/tags", timeout=timeout) as r:
            body = _json.loads(r.read().decode())
    except Exception:
        return []

    seen: set = set()
    models: List[Dict] = []
    for m in body.get("models", []) or []:
        mid = m.get("name") or m.get("model")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        details = m.get("details") or {}
        param_size = details.get("parameter_size") or ""
        family = details.get("family") or mid.split(":")[0]
        pretty = f"{family.title()} {param_size}".strip() or mid
        models.append({"id": mid, "name": f"{pretty} (local)", "in": 0.0, "out": 0.0})
    return models


def list_providers() -> List[Dict]:
    """Return a UI-friendly list of providers with model metadata."""
    out = []
    discovered_ollama = discover_ollama_models()
    for pid, spec in PROVIDER_CATALOG.items():
        env = spec.get("env")
        available = (env is None) or bool(os.getenv(env))
        models = spec["models"]
        # Live-discovered Ollama models trump the static fallback
        if pid == "ollama" and discovered_ollama:
            models = discovered_ollama
            available = True
        elif pid == "ollama" and not discovered_ollama:
            available = False  # daemon unreachable — show as not connected
        out.append({
            "id": pid,
            "label": spec["label"],
            "kind": spec["kind"],
            "env": env,
            "available": available,
            "models": models,
        })
    return out


def models_for(provider: str) -> List[Dict]:
    """Return the active model list for a provider, with live Ollama discovery."""
    if provider == "ollama":
        discovered = discover_ollama_models()
        if discovered:
            return discovered
    return PROVIDER_CATALOG.get(provider, {}).get("models", [])


def _price_for(provider: str, model: str, in_tok: int, out_tok: int) -> float:
    for m in models_for(provider):
        if m["id"] == model:
            return (in_tok / 1_000_000) * m["in"] + (out_tok / 1_000_000) * m["out"]
    return 0.0


# ─────────────────────────────────────────────────────────────
# Concrete providers
# ─────────────────────────────────────────────────────────────

def _stub_complete(req: CompletionRequest, provider: str) -> CompletionResponse:
    """Deterministic offline stub so the product demos without API keys."""
    snippet = req.user.strip().splitlines()[0][:120] if req.user.strip() else "your request"
    text = (
        f"[{provider}:{req.model}] (offline stub)\n\n"
        f"I would respond here once an API key for '{provider}' is configured. "
        f"You asked: “{snippet}”.\n\n"
        f"With the system policy: “{req.system.splitlines()[0][:140] if req.system else 'default'}”."
    )
    in_tok = max(1, len(req.system + req.user) // 4)
    out_tok = max(1, len(text) // 4)
    return CompletionResponse(
        text=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_price_for(provider, req.model, in_tok, out_tok),
        provider=provider,
        model=req.model,
        stubbed=True,
    )


def _complete_anthropic(req: CompletionRequest) -> CompletionResponse:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _stub_complete(req, "anthropic")
    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=req.model,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            system=req.system or "You are a helpful assistant.",
            messages=[*req.history, {"role": "user", "content": req.user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        in_tok = getattr(msg.usage, "input_tokens", 0)
        out_tok = getattr(msg.usage, "output_tokens", 0)
        return CompletionResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=_price_for("anthropic", req.model, in_tok, out_tok),
            provider="anthropic", model=req.model,
        )
    except Exception:
        logger.warning("llm.anthropic_call_failed — falling back to stub", exc_info=True)
        return _stub_complete(req, "anthropic")


def _complete_openai_compatible(req: CompletionRequest, provider: str, base_url: Optional[str], env_var: Optional[str]) -> CompletionResponse:
    api_key = os.getenv(env_var) if env_var else "local"
    if env_var and not api_key:
        return _stub_complete(req, provider)
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=api_key or "local", base_url=base_url)
        resp = client.chat.completions.create(
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            messages=[
                {"role": "system", "content": req.system or "You are a helpful assistant."},
                *req.history,
                {"role": "user", "content": req.user},
            ],
        )
        text = resp.choices[0].message.content or ""
        in_tok = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
        out_tok = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
        return CompletionResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=_price_for(provider, req.model, in_tok, out_tok),
            provider=provider, model=req.model,
        )
    except Exception:
        logger.warning("llm.%s_call_failed — falling back to stub", provider, exc_info=True)
        return _stub_complete(req, provider)


def _complete_ollama(req: CompletionRequest) -> CompletionResponse:
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import urllib.request, json
        payload = {
            "model": req.model,
            "prompt": f"{req.system}\n\nUser: {req.user}\n\nAssistant:",
            "stream": False,
            "options": {"temperature": req.temperature, "num_predict": req.max_tokens},
        }
        request = urllib.request.Request(
            f"{base.rstrip('/')}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as r:
            body = json.loads(r.read().decode())
        text = body.get("response", "")
        return CompletionResponse(
            text=text,
            input_tokens=body.get("prompt_eval_count", 0),
            output_tokens=body.get("eval_count", 0),
            cost_usd=0.0, provider="ollama", model=req.model,
        )
    except Exception:
        logger.warning("llm.ollama_call_failed — falling back to stub", exc_info=True)
        return _stub_complete(req, "ollama")


_DISPATCH: Dict[str, Callable[[CompletionRequest], CompletionResponse]] = {
    "anthropic": _complete_anthropic,
    "openai": lambda r: _complete_openai_compatible(r, "openai", None, "OPENAI_API_KEY"),
    "google": lambda r: _complete_openai_compatible(
        r, "google", "https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"
    ),
    "mistral": lambda r: _complete_openai_compatible(
        r, "mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"
    ),
    "groq": lambda r: _complete_openai_compatible(
        r, "groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"
    ),
    "ollama": _complete_ollama,
    "lmstudio": lambda r: _complete_openai_compatible(
        r, "lmstudio", os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"), None
    ),
}


def complete(req: CompletionRequest, provider: str) -> CompletionResponse:
    """Dispatch a completion to the chosen provider, measuring latency."""
    fn = _DISPATCH.get(provider, lambda r: _stub_complete(r, provider))
    t0 = time.time()
    resp = fn(req)
    resp.latency_ms = int((time.time() - t0) * 1000)
    return resp
