"""Pluggable text-embedding seam, mirroring how llm_providers abstracts completion.

Backend selected by EMBEDDING_BACKEND:
  - "stub"                 deterministic hash vectors; no deps, no network (tests/offline)
  - "sentence_transformers" local all-MiniLM-L6-v2 (prod default; guarded import)
  - "ollama"               local Ollama embeddings endpoint

Any real-backend failure falls back to the stub with a logged warning, so a
missing model or daemon never breaks a memory write or a run.
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
from typing import List

logger = logging.getLogger(__name__)

STUB_DIM = 64
Vector = List[float]


def _stub_embed(texts: List[str]) -> List[Vector]:
    """Deterministic pseudo-embedding: hash → fixed-length float vector in [0,1)."""
    out: List[Vector] = []
    for t in texts:
        vec: Vector = []
        counter = 0
        # Expand the text hash until we have STUB_DIM floats.
        while len(vec) < STUB_DIM:
            h = hashlib.sha256(f"{t}#{counter}".encode("utf-8")).digest()
            for i in range(0, len(h), 4):
                if len(vec) >= STUB_DIM:
                    break
                (n,) = struct.unpack("I", h[i:i + 4])
                vec.append((n % 10_000) / 10_000.0)
            counter += 1
        out.append(vec)
    return out


def _sentence_transformers_embed(texts: List[str]) -> List[Vector]:
    from sentence_transformers import SentenceTransformer  # guarded import
    global _ST_MODEL
    try:
        _ST_MODEL
    except NameError:
        _ST_MODEL = SentenceTransformer(
            os.getenv("ST_EMBED_MODEL", "all-MiniLM-L6-v2")
        )
    return [list(map(float, v)) for v in _ST_MODEL.encode(texts)]


def _ollama_embed(texts: List[str]) -> List[Vector]:
    import json
    import urllib.request

    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    out: List[Vector] = []
    for t in texts:
        payload = json.dumps({"model": model, "prompt": t}).encode()
        req = urllib.request.Request(
            f"{base}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        out.append([float(x) for x in body["embedding"]])
    return out


def embed(texts: List[str]) -> List[Vector]:
    """Embed a list of texts using the configured backend (fail-open to stub)."""
    if not texts:
        return []
    backend = os.getenv("EMBEDDING_BACKEND", "sentence_transformers")
    if backend == "stub":
        return _stub_embed(texts)
    try:
        if backend == "sentence_transformers":
            return _sentence_transformers_embed(texts)
        if backend == "ollama":
            return _ollama_embed(texts)
        logger.warning("embeddings.unknown_backend=%s — using stub", backend)
        return _stub_embed(texts)
    except Exception:
        logger.warning("embeddings.%s_failed — falling back to stub", backend, exc_info=True)
        return _stub_embed(texts)
