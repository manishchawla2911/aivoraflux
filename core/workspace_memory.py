"""Shared workspace memory: canonical in SQLite, semantically indexed in ChromaDB.

SQLite (WorkspaceMemory) is the source of truth. Each entry id is mirrored as a
ChromaDB document (embedding + content + metadata) in a per-workspace collection
so agents can retrieve relevant memory by similarity. Every ChromaDB interaction
is fail-open: writes that fail are logged and dropped; queries that fail fall back
to recent-rows SQL filtering. Nothing here can break an agent run.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import List, Optional

from sqlmodel import Session, select

from core.embeddings import embed
from core.state import Workspace, WorkspaceMemory, utcnow

logger = logging.getLogger(__name__)


def _chroma_client():
    import chromadb  # guarded: absent lib → caller's try/except triggers SQL fallback
    path = os.getenv("CHROMA_DIR", "./chroma")
    return chromadb.PersistentClient(path=path)


def _collection_name(workspace_id: str) -> str:
    # Chroma names: 3-63 chars, alnum/._-, start & end alphanumeric.
    safe = "".join(c if c.isalnum() else "-" for c in workspace_id)
    return f"ws-{safe}"[:63].rstrip("-")


def _get_collection(workspace_id: str):
    client = _chroma_client()
    return client.get_or_create_collection(_collection_name(workspace_id))


def add_entry(
    session: Session,
    workspace_id: str,
    *,
    author: Optional[str],
    kind: str,
    content: str,
    tags: Optional[List[str]] = None,
) -> WorkspaceMemory:
    """Write a memory entry to SQLite, then mirror it into ChromaDB (fail-open)."""
    entry = WorkspaceMemory(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        author=author,
        kind=kind,
        content=content,
        tags=json.dumps(tags or []),
        created_at=utcnow(),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    try:
        vec = embed([content])
        _get_collection(workspace_id).add(
            ids=[entry.id],
            embeddings=vec,
            documents=[content],
            metadatas=[{"kind": kind, "author": author or "", "tags": entry.tags}],
        )
    except Exception:
        logger.warning("workspace_memory.chroma_add_failed ws=%s", workspace_id, exc_info=True)

    return entry


def _recent_rows(session: Session, workspace_id: str, k: int,
                 kind: Optional[str]) -> List[WorkspaceMemory]:
    stmt = select(WorkspaceMemory).where(WorkspaceMemory.workspace_id == workspace_id)
    if kind:
        stmt = stmt.where(WorkspaceMemory.kind == kind)
    stmt = stmt.order_by(WorkspaceMemory.created_at.desc()).limit(k)
    return list(session.exec(stmt).all())


def query(
    session: Session,
    workspace_id: str,
    text: str,
    k: int = 5,
    kind: Optional[str] = None,
) -> List[WorkspaceMemory]:
    """Return up to k entries most relevant to `text` (semantic, else recent rows)."""
    try:
        qvec = embed([text])
        res = _get_collection(workspace_id).query(query_embeddings=qvec, n_results=k)
        ids = (res.get("ids") or [[]])[0]
        if not ids:
            return _recent_rows(session, workspace_id, k, kind)
        # Hydrate from SQLite, preserving Chroma's relevance order.
        rows = {
            r.id: r for r in session.exec(
                select(WorkspaceMemory).where(WorkspaceMemory.id.in_(ids))
            ).all()
        }
        ordered = [rows[i] for i in ids if i in rows]
        if kind:
            ordered = [r for r in ordered if r.kind == kind]
        return ordered or _recent_rows(session, workspace_id, k, kind)
    except Exception:
        logger.warning("workspace_memory.chroma_query_failed ws=%s — SQL fallback",
                       workspace_id, exc_info=True)
        return _recent_rows(session, workspace_id, k, kind)


def build_memory_context(session: Session, workspace_id: str, query_text: str,
                         k: int = 5) -> str:
    """Format mission + top-k relevant entries into a system-prompt memory block.

    Returns "" for an unknown/empty workspace so callers can prepend unconditionally.
    """
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        return ""
    entries = query(session, workspace_id, query_text, k=k)
    if ws.mission is None and not entries:
        return ""
    lines = ["## Shared workspace memory",
             "Use this shared context to stay aligned with the rest of the company."]
    if ws.mission:
        lines.append(f"Mission: {ws.mission}")
    for e in entries:
        author = e.author or "unknown"
        lines.append(f"- [{e.kind}] ({author}) {e.content}")
    return "\n".join(lines)
