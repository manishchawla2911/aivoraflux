"""Fail-open email sender for the marketing agent.

EMAIL_BACKEND selects the backend: "stub" (default; logs and returns True so outreach
flows complete offline) or "smtp" (uses SMTP_* env; any error → logs + False). Mirrors
core.notifier.SlackNotifier's fail-open philosophy — never raises.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
            timeout = float(os.getenv("SMTP_TIMEOUT_SECONDS", "15"))
            use_ssl = _env_bool("SMTP_USE_SSL", port == 465)
            use_starttls = _env_bool("SMTP_STARTTLS", not use_ssl)
            context = ssl.create_default_context() if use_ssl or use_starttls else None
            if use_ssl:
                def server_factory(): return smtplib.SMTP_SSL(
                    host, port, timeout=timeout, context=context)
            else:
                def server_factory(): return smtplib.SMTP(host, port, timeout=timeout)
            with server_factory() as server:
                if use_starttls:
                    server.starttls(context=context)
                user = os.getenv("SMTP_USER", "")
                password = os.getenv("SMTP_PASSWORD", "")
                if user:
                    server.login(user, password)
                server.send_message(msg)
            return True
        except Exception:
            logger.warning(
                "email.smtp_send_failed host=%s port=%s ssl=%s starttls=%s",
                host,
                os.getenv("SMTP_PORT", "587"),
                _env_bool("SMTP_USE_SSL", os.getenv(
                    "SMTP_PORT", "587") == "465"),
                _env_bool("SMTP_STARTTLS", True),
                exc_info=True,
            )
            return False

    logger.warning("email.unknown_backend=%s — treating as stub", backend)
    return True
