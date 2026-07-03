"""Admin alerting (Phase 5).

The spec asks for an admin alert (email or log alert) when a run fails after
retries are exhausted. We always emit a clearly-marked log alert and, if
ADMIN_ALERT_WEBHOOK is configured, POST a best-effort JSON payload (e.g. a Slack
incoming webhook). Alerting never raises — a failed alert must not mask the
original failure.
"""

import logging

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)


async def alert_admin(message: str, *, run_id: str | None = None) -> None:
    detail = f"run={run_id} " if run_id else ""
    logger.error("ADMIN ALERT: %s%s", detail, message)

    webhook = get_settings().admin_alert_webhook
    if not webhook:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook, json={"text": f"Cadence ADMIN ALERT: {detail}{message}"})
    except Exception:  # noqa: BLE001 - alerting is best-effort
        logger.exception("failed to deliver admin alert webhook")
