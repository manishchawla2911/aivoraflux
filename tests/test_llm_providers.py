"""Tests for the pluggable LLM provider registry (core/llm_providers.py)."""
from __future__ import annotations

import pytest

from core.llm_providers import (
    CompletionRequest,
    _price_for,
    complete,
    list_providers,
    models_for,
)


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    """Ensure cloud keys are absent so calls fall back to the offline stub."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                "MISTRAL_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _req() -> CompletionRequest:
    return CompletionRequest(
        system="You are a helpful assistant.",
        user="Hello world, please respond.",
        model="claude-sonnet-4-6",
    )


def test_anthropic_without_key_returns_stub():
    resp = complete(_req(), "anthropic")
    assert resp.stubbed is True
    assert "offline stub" in resp.text
    assert resp.input_tokens > 0
    assert resp.output_tokens > 0
    assert resp.latency_ms >= 0
    assert resp.provider == "anthropic"


def test_unknown_provider_falls_back_to_stub():
    resp = complete(_req(), "totally-made-up")
    assert resp.stubbed is True
    assert resp.provider == "totally-made-up"


def test_price_for_known_model():
    # Sonnet 4.6: $3 / Mtok in, $15 / Mtok out.
    assert _price_for("anthropic", "claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)
    # Unknown model → no price.
    assert _price_for("anthropic", "nope", 1_000_000, 0) == 0.0


def test_models_for():
    models = models_for("anthropic")
    ids = {m["id"] for m in models}
    assert "claude-opus-4-7" in ids
    assert models_for("does-not-exist") == []


def test_list_providers_availability():
    providers = {p["id"]: p for p in list_providers()}
    assert "anthropic" in providers
    # No key set → cloud provider unavailable.
    assert providers["anthropic"]["available"] is False
    # Local providers need no key → always available.
    assert providers["ollama"]["available"] is True
