"""Seed a few demo projects + one pending decision so the dashboard isn't empty.

Run with:
    python scripts/seed_demo.py
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import Session  # noqa: E402

from core.state import HumanDecision, Project, get_engine, init_db, utcnow  # noqa: E402


def seed() -> None:
    init_db()
    with Session(get_engine()) as s:
        now = utcnow()

        projects = [
            Project(
                id=str(uuid.uuid4()),
                name="Acme Order Portal",
                client_name="Acme Industries",
                status="BUILDING",
                created_at=now - timedelta(days=2, hours=4),
                updated_at=now - timedelta(hours=1),
            ),
            Project(
                id=str(uuid.uuid4()),
                name="Beacon Notification Service",
                client_name="Beacon Health",
                status="AWAITING_PRD_APPROVAL",
                created_at=now - timedelta(hours=6),
                updated_at=now - timedelta(hours=1),
            ),
            Project(
                id=str(uuid.uuid4()),
                name="Internal CRM Lite",
                client_name=None,
                status="DELIVERED",
                created_at=now - timedelta(days=14),
                updated_at=now - timedelta(days=8),
            ),
            Project(
                id=str(uuid.uuid4()),
                name="Pinecrest Booking System",
                client_name="Pinecrest Hotels",
                status="VALIDATING",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(minutes=20),
            ),
        ]

        for p in projects:
            existing = s.get(Project, p.id)
            if not existing:
                s.add(p)
        s.commit()

        # Add a pending PRD-approval decision to the second project
        beacon = projects[1]
        decision = HumanDecision(
            id=str(uuid.uuid4()),
            project_id=beacon.id,
            decision_type="prd_approval",
            context=json.dumps({
                "summary": "PRD draft ready for review",
                "highlights": [
                    "Twilio SMS for outbound notifications",
                    "Fallback to email after 2 failed SMS attempts",
                    "Rate-limited at 5 messages per recipient per hour",
                ],
                "open_questions_resolved": 4,
            }, indent=2),
            options=json.dumps(["approve", "request changes", "reject"]),
            notified_at=now - timedelta(hours=2),
        )
        s.add(decision)
        s.commit()

        print(f"Seeded {len(projects)} projects.")
        print("Open http://localhost:8000 to view.")


if __name__ == "__main__":
    seed()
