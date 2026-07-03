from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App config, populated from environment variables (or backend/.env locally).

    Field names map to env vars case-insensitively (e.g. database_url <- DATABASE_URL),
    matching the bare DATABASE_URL Railway's Postgres plugin injects.
    """

    database_url: str = "postgresql://postgres:postgres@localhost:5432/cadence"
    default_market: str = "health & wellness, underdeveloped and emerging markets"

    # --- Phase 2: research + uniqueness guard -------------------------------
    # The handoff spec deliberately uses a cheaper model for extraction
    # ("cheaper model fine for extraction"); Haiku still accepts `temperature`,
    # which the spec wants low (~0.3) so extraction stays grounded.
    research_model: str = "claude-haiku-4-5"
    research_temperature: float = 0.3
    research_max_tokens: int = 1024

    # Uniqueness guard for pain points: reject a new pain point if its token-set
    # similarity to any existing one exceeds this (handoff README Phase 2 = 0.70).
    novelty_threshold: float = 0.70
    research_max_retries: int = 5

    # Search/news provider for fresh source material.
    # Options: "gdelt" (keyless, default), "newsapi", "serpapi", "fake".
    search_provider: str = "gdelt"
    search_max_results: int = 8
    search_query: Optional[str] = None  # override the query derived from the market
    search_timespan: str = "3m"  # GDELT lookback window
    serpapi_key: Optional[str] = None
    newsapi_key: Optional[str] = None

    # --- Phase 3: content generation + hard uniqueness ----------------------
    # The handoff spec uses a "claude-sonnet class" model for content, ~0.7 temp.
    content_model: str = "claude-sonnet-4-6"
    content_temperature: float = 0.7
    content_max_tokens: int = 4000

    # Cosine-similarity threshold for the content_registry uniqueness check.
    # The spec's 0.30 assumes a 1536-dim *semantic* embedding model; the shipped
    # HashingEmbedder is lexical (term-frequency), whose baseline overlap runs
    # higher (shared domain vocabulary + the identical lead-magnet CTA), so the
    # default is tuned up. Swap in a semantic Embedder and lower this toward 0.30.
    content_sim_threshold: float = 0.60
    content_max_retries: int = 4

    # --- Phase 4: lead capture + ActiveCampaign + email nurture -------------
    # When ac_base_url + ac_api_token are set the real ActiveCampaign v3 client is
    # used; otherwise a keyless stub stands in (so the lead flow runs in dev).
    ac_base_url: Optional[str] = None  # e.g. https://<account>.api-us1.com
    ac_api_token: Optional[str] = None
    ac_list_id: Optional[str] = None
    ac_field_lead_source: Optional[str] = None  # AC custom-field id for lead_source
    ac_field_pain_point: Optional[str] = None  # AC custom-field id for pain_point
    sender_name: str = "The Cadence Team"

    # --- Phase 5: production hardening --------------------------------------
    # Retry-with-exponential-backoff for every external call (search, Claude, AC).
    retry_attempts: int = 4
    retry_base_delay: float = 1.0  # seconds; doubled each attempt
    retry_max_delay: float = 16.0
    # Optional admin alert webhook hit when a run fails after retries are exhausted.
    admin_alert_webhook: Optional[str] = None

    # Human approval gate (addendum): when true (default), a real run generates +
    # audits but holds at status='review' — nothing distributes until a person
    # approves and releases it via POST /runs/{run_id}/publish. False = auto-publish
    # on pass. A per-run `approval_gate` in the POST body overrides this.
    human_approval_gate: bool = True

    # Real distribution channels (optional, when creds exist). Each platform falls
    # back to the console stub when its credentials aren't configured.
    wp_base_url: Optional[str] = None  # WordPress site, e.g. https://blog.example.com
    wp_username: Optional[str] = None
    wp_app_password: Optional[str] = None  # WordPress application password
    linkedin_access_token: Optional[str] = None
    linkedin_author_urn: Optional[str] = None  # e.g. urn:li:organization:123
    facebook_page_id: Optional[str] = None
    facebook_access_token: Optional[str] = None
    # Instagram feed posts are media-based and need an image + app review; left
    # to the stub by default. See app/distributors.py.
    instagram_business_id: Optional[str] = None
    instagram_access_token: Optional[str] = None

    # ANTHROPIC_API_KEY is read directly by the Anthropic SDK from the
    # environment; it doesn't need to be declared here.

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def retry_policy(self) -> "RetryPolicy":
        from .retry import RetryPolicy

        return RetryPolicy(
            attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            max_delay=self.retry_max_delay,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
