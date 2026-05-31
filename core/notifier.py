"""Slack webhook notifier for human decision points."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


SLACK_TEMPLATE = (
    "🔔 *[Project: {project_name}]* needs your input\n\n"
    "*Type*: {decision_type}\n"
    "*Summary*: {summary}\n\n"
    "→ Review & decide: {context_url}"
)


class SlackNotifier:
    """Posts to Slack via incoming webhook.

    If SLACK_WEBHOOK_URL is unset, calls become no-ops with a warning log
    so the orchestrator can run in environments without Slack configured.
    """

    def __init__(self, webhook_url: Optional[str] = None, dashboard_url: str = ""):
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")
        self.dashboard_url = dashboard_url or os.getenv("DASHBOARD_URL", "http://localhost:8000")

    async def send_decision_request(
        self,
        project_id: str,
        decision_type: str,
        context_url: str,
        project_name: str = "",
        summary: str = "",
    ) -> bool:
        text = SLACK_TEMPLATE.format(
            project_name=project_name or project_id,
            decision_type=decision_type,
            summary=summary or "Decision required",
            context_url=context_url,
        )
        return await self._post({"text": text})

    async def send_reminder(self, project_id: str, decision_id: str) -> bool:
        text = (
            f"⏰ Reminder: project `{project_id}` is still waiting on decision "
            f"`{decision_id}`. → {self.dashboard_url}/projects/{project_id}/decisions/{decision_id}"
        )
        return await self._post({"text": text})

    async def _post(self, payload: dict) -> bool:
        if not self.webhook_url:
            logger.warning("notifier.slack_webhook_missing — skipping send")
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.webhook_url, json=payload)
            if resp.status_code >= 300:
                logger.error(
                    "notifier.slack_post_failed",
                    extra={"status": resp.status_code, "body": resp.text[:200]},
                )
                return False
            return True
        except Exception:
            logger.exception("notifier.slack_post_exception")
            return False
