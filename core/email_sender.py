"""Fail-open email sender for the marketing agent.

EMAIL_BACKEND selects the backend: "stub" (default; logs and returns True so outreach
flows complete offline) or "smtp" (uses SMTP_* env; any error → logs + False). Mirrors
core.notifier.SlackNotifier's fail-open philosophy — never raises.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str, *, backend: Optional[str] = None) -> bool:
    """Send an email. Fail-open: missing config / errors → log + False, never raises."""
    backend = backend or os.getenv("EMAIL_BACKEND", "stub")

    if backend == "stub":
        logger.info("email.stub_send to=%s subject=%s", to, subject)
        return True

    if backend == "smtp":
        host = os.getenv("SMTP_HOST", "")
        if not host:
            logger.warning("email.smtp_host_missing — skipping send")
            return False
        try:
            msg = EmailMessage()
            msg["From"] = os.getenv("SMTP_FROM", "noreply@example.com")
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                user = os.getenv("SMTP_USER", "")
                password = os.getenv("SMTP_PASSWORD", "")
                if user:
                    server.login(user, password)
                server.send_message(msg)
            return True
        except Exception:
            logger.warning("email.smtp_send_failed", exc_info=True)
            return False

    logger.warning("email.unknown_backend=%s — treating as stub", backend)
    return True
