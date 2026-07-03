"""Phase 4 email nurture sequence drafting.

After a lead is synced to ActiveCampaign, draft a 3-email nurture sequence
(deliver / educate / soft-pitch) via the Email Sequence prompt and save it as a
draft campaign. Persistence is behind a `CampaignStore` protocol so the drafting
logic is unit-testable without Postgres.
"""

import logging
from typing import Optional, Protocol

from . import prompts
from .llm import LLMClient

logger = logging.getLogger(__name__)


class EmailDraftError(RuntimeError):
    pass


class CampaignStore(Protocol):
    async def insert_campaign(self, run_id: Optional[str], lead_id: int) -> int: ...

    async def insert_campaign_email(
        self,
        campaign_id: int,
        position: int,
        goal: str,
        timing: str,
        subject: str,
        body: str,
    ) -> None: ...


class EmailSequenceService:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        store: CampaignStore,
        model: str,
        temperature: float,
        max_tokens: int,
        sender_name: str,
    ) -> None:
        self._llm = llm_client
        self._store = store
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._sender_name = sender_name

    async def draft(
        self,
        run_id: Optional[str],
        lead_id: int,
        pain_point: Optional[str],
        lead_magnet_title: Optional[str],
    ) -> int:
        """Draft and persist a 3-email sequence as a 'draft' campaign; return its id."""
        user = prompts.EMAIL_USER.substitute(
            pain_point=pain_point or "the reader's challenge",
            lead_magnet_title=lead_magnet_title or "your free guide",
            sender_name=self._sender_name,
        )
        data = await self._llm.complete_json(
            system=prompts.EMAIL_SYSTEM,
            user=user,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        emails = data.get("emails") or []
        if not emails:
            raise EmailDraftError("email sequence response contained no emails")

        campaign_id = await self._store.insert_campaign(run_id, lead_id)
        for e in emails:
            await self._store.insert_campaign_email(
                campaign_id,
                int(e.get("position") or 0),
                e.get("goal", ""),
                e.get("timing", ""),
                e.get("subject", ""),
                e.get("body", ""),
            )
        logger.info("drafted campaign #%s (%d emails) for lead %s", campaign_id, len(emails), lead_id)
        return campaign_id
