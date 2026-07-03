"""ActiveCampaign CRM client (Phase 4).

Swappable behind the `ActiveCampaignClient` protocol (same pattern as the search
and distribution interfaces). A keyless stub is the default so lead capture works
in dev; the real v3 HTTP client is used when AC_BASE_URL + AC_API_TOKEN are set.
"""

import hashlib
import logging
from typing import Optional, Protocol

import httpx

from .config import Settings
from .retry import RetryPolicy, is_retryable_httpx, retry_async

logger = logging.getLogger(__name__)


class ActiveCampaignError(RuntimeError):
    pass


class ActiveCampaignClient(Protocol):
    async def sync_contact(
        self, *, name: str, email: str, lead_source: Optional[str], pain_point: Optional[str]
    ) -> str:
        """Create or update a contact; return the contact id."""
        ...


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


class StubActiveCampaign:
    """Logs instead of calling ActiveCampaign; returns a deterministic stub id."""

    async def sync_contact(
        self, *, name: str, email: str, lead_source: Optional[str], pain_point: Optional[str]
    ) -> str:
        contact_id = "stub-" + hashlib.sha1(email.lower().encode("utf-8")).hexdigest()[:10]
        logger.info(
            "[activecampaign stub] sync %s -> %s (lead_source=%s)", email, contact_id, lead_source
        )
        return contact_id


class HttpActiveCampaign:
    """ActiveCampaign API v3. Upserts via /contact/sync, attaches custom fields,
    and optionally adds the contact to a list."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        list_id: Optional[str] = None,
        field_lead_source: Optional[str] = None,
        field_pain_point: Optional[str] = None,
        policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = api_token
        self._list_id = list_id
        self._field_lead_source = field_lead_source
        self._field_pain_point = field_pain_point
        self._policy = policy

    async def sync_contact(
        self, *, name: str, email: str, lead_source: Optional[str], pain_point: Optional[str]
    ) -> str:
        first, last = _split_name(name)
        field_values = []
        if self._field_lead_source and lead_source:
            field_values.append({"field": self._field_lead_source, "value": lead_source})
        if self._field_pain_point and pain_point:
            field_values.append({"field": self._field_pain_point, "value": pain_point})

        contact: dict = {"email": email, "firstName": first, "lastName": last}
        if field_values:
            contact["fieldValues"] = field_values

        headers = {"Api-Token": self._token, "Content-Type": "application/json"}

        async def _sync() -> str:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/3/contact/sync", json={"contact": contact}, headers=headers
                )
                resp.raise_for_status()
                contact_id = (resp.json().get("contact") or {}).get("id")
                if not contact_id:
                    raise ActiveCampaignError("contact/sync returned no contact id")

                if self._list_id:
                    list_resp = await client.post(
                        f"{self._base}/api/3/contactLists",
                        json={"contactList": {"list": self._list_id, "contact": contact_id, "status": 1}},
                        headers=headers,
                    )
                    list_resp.raise_for_status()
            return str(contact_id)

        contact_id = await retry_async(
            _sync, policy=self._policy, should_retry=is_retryable_httpx, description="ActiveCampaign sync"
        )
        logger.info("[activecampaign] synced %s -> contact %s", email, contact_id)
        return contact_id


def build_ac_client(settings: Settings) -> ActiveCampaignClient:
    if settings.ac_base_url and settings.ac_api_token:
        return HttpActiveCampaign(
            settings.ac_base_url,
            settings.ac_api_token,
            list_id=settings.ac_list_id,
            field_lead_source=settings.ac_field_lead_source,
            field_pain_point=settings.ac_field_pain_point,
            policy=settings.retry_policy(),
        )
    return StubActiveCampaign()
