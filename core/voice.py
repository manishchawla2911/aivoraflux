"""Pluggable speech providers (STT/TTS) for the marketing agent, fail-open to a stub.

VOICE_BACKEND selects the backend: "stub" (default; deterministic, no deps/network),
"openai" (Whisper STT + TTS), "elevenlabs" (TTS). Any real-backend failure falls back to
the stub with a logged warning, so missing keys/SDKs never break a flow or a test.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _stub_synthesize(text: str) -> bytes:
    # Deterministic, non-empty, derived from the text.
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return b"AUDIO:" + text.encode("utf-8") + b":" + digest[:8]


def _stub_transcribe(audio: bytes) -> str:
    return f"[transcript:{len(audio)} bytes]"


def synthesize(text: str, *, backend: Optional[str] = None) -> bytes:
    """Text → speech audio bytes (fail-open to stub)."""
    backend = backend or os.getenv("VOICE_BACKEND", "stub")
    if backend == "stub":
        return _stub_synthesize(text)
    try:
        if backend == "openai":
            from openai import OpenAI  # guarded
            client = OpenAI()
            resp = client.audio.speech.create(
                model=os.getenv("OPENAI_TTS_MODEL", "tts-1"),
                voice=os.getenv("OPENAI_TTS_VOICE", "alloy"), input=text,
            )
            return resp.read()
        if backend == "elevenlabs":
            import httpx
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
            key = os.getenv("ELEVENLABS_API_KEY", "")
            r = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": key}, json={"text": text}, timeout=30,
            )
            return r.content
        logger.warning("voice.unknown_backend=%s — using stub", backend)
        return _stub_synthesize(text)
    except Exception:
        logger.warning("voice.%s_synthesize_failed — stub", backend, exc_info=True)
        return _stub_synthesize(text)


def transcribe(audio: bytes, *, backend: Optional[str] = None) -> str:
    """Speech audio bytes → text (fail-open to stub)."""
    backend = backend or os.getenv("VOICE_BACKEND", "stub")
    if backend == "stub":
        return _stub_transcribe(audio)
    try:
        if backend == "openai":
            import io
            from openai import OpenAI  # guarded
            client = OpenAI()
            buf = io.BytesIO(audio)
            buf.name = "audio.wav"
            resp = client.audio.transcriptions.create(
                model=os.getenv("OPENAI_STT_MODEL", "whisper-1"), file=buf,
            )
            return resp.text
        logger.warning("voice.unknown_backend=%s — using stub", backend)
        return _stub_transcribe(audio)
    except Exception:
        logger.warning("voice.%s_transcribe_failed — stub", backend, exc_info=True)
        return _stub_transcribe(audio)
