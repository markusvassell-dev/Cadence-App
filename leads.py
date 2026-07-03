import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db
from ..activecampaign import ActiveCampaignClient, build_ac_client
from ..config import get_settings
from ..email_sequence import EmailSequenceService
from ..landing import render_lead_success_html
from ..leads import LeadValidationError, validate_lead
from ..llm import AnthropicLLMClient

logger = logging.getLogger(__name__)
router = APIRouter()


@lru_cache
def get_ac_client() -> ActiveCampaignClient:
    return build_ac_client(get_settings())


@lru_cache
def get_email_service() -> EmailSequenceService:
    settings = get_settings()
    return EmailSequenceService(
        llm_client=AnthropicLLMClient(settings.retry_policy()),
        store=db.DbCampaignStore(),
        model=settings.content_model,
        temperature=settings.content_temperature,
        max_tokens=settings.content_max_tokens,
        sender_name=settings.sender_name,
    )


async def _read_payload(request: Request) -> tuple[dict, bool]:
    """Return (data, is_form). Supports the landing-page form post and JSON clients."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        return await request.json(), False
    form = await request.form()
    return {k: v for k, v in form.items()}, True


def _as_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@router.post("/leads")
async def create_lead(
    request: Request,
    ac_client: ActiveCampaignClient = Depends(get_ac_client),
    email_service: EmailSequenceService = Depends(get_email_service),
):
    """Capture a lead from the lead-magnet form: validate, sync to ActiveCampaign,
    persist, and (on a successful sync) draft the 3-email nurture sequence."""
    data, is_form = await _read_payload(request)
    try:
        name, email = validate_lead(data.get("name"), data.get("email"))
    except LeadValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    run_id = (data.get("lead_source") or "").strip() or None
    if run_id and not await db.run_exists(run_id):
        # Tolerate an unknown/edited run id — still capture the lead.
        run_id = None
    content_id = _as_int(data.get("content_id"))
    pain_point = data.get("pain_point") or (await db.get_run_pain_point(run_id) if run_id else None)

    # Lead-magnet title (for the email) comes from the landing page the form was on.
    lead_magnet_title = None
    slug = (data.get("slug") or "").strip()
    if slug:
        lm = await db.get_lead_magnet_by_slug(slug)
        if lm:
            lead_magnet_title = lm["headline"]
            if run_id is None:
                run_id = lm["run_id"]

    # Sync to ActiveCampaign (create/update contact with custom fields).
    try:
        ac_contact_id = await ac_client.sync_contact(
            name=name, email=email, lead_source=run_id, pain_point=pain_point
        )
        sync_status = "synced"
    except Exception as exc:
        logger.exception("ActiveCampaign sync failed for %s", email)
        ac_contact_id, sync_status = None, "failed"

    lead_id = await db.insert_lead(
        run_id, content_id, name, email, pain_point, ac_contact_id, sync_status
    )

    # Draft the nurture sequence only after a successful contact push (best-effort).
    campaign_id = None
    if sync_status == "synced":
        try:
            campaign_id = await email_service.draft(run_id, lead_id, pain_point, lead_magnet_title)
        except Exception:
            logger.exception("email sequence drafting failed for lead %s", lead_id)

    if is_form:
        return HTMLResponse(content=render_lead_success_html(name))
    return {
        "lead_id": lead_id,
        "sync_status": sync_status,
        "ac_contact_id": ac_contact_id,
        "campaign_id": campaign_id,
    }


@router.get("/leads")
async def list_leads(limit: int = 50):
    """Captured leads, newest first (for the admin console's Leads screen)."""
    return {"leads": await db.list_leads(limit=limit)}
