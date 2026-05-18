"""Environment configuration loaded via pydantic-settings."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Meta Cloud API
    meta_app_secret: str
    meta_verify_token: str
    meta_access_token: str
    meta_phone_number_id: str
    meta_waba_id: str = ""

    # Server
    port: int = 8000
    log_level: str = "INFO"
    public_base_url: str = ""
    railway_service_web_url: str = ""

    # v0.3 persistence/admin portal
    database_url: str = ""
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_session_ttl_seconds: int = 60 * 60 * 24 * 7
    # Whether the admin session cookie is emitted with the Secure flag.
    # Default True so Railway production gets a hardened cookie; flip to
    # False for localhost http dev and the test client (ewash-mfx).
    admin_cookie_secure: bool = True
    internal_cron_secret: str = ""
    admin_default_locale: str = "fr"
    # Optional single-owner cash ledger assistant. When set to Omar's normalized
    # WhatsApp number (digits only), IDLE WhatsApp messages from that phone that
    # look like cash movements are captured into the operational cash ledger.
    cash_ledger_owner_phone: str = ""

    # CORS for the PWA — comma-separated exact origins.
    allowed_origins: str = ""
    # Single regex for branch-deploy preview URLs,
    # e.g. ``^https://ewash-mobile-app-.*\.vercel\.app$``.
    allowed_origin_regex: str = ""

    # Feature flag — disables the /api/v1 router entirely when False.
    # Accepts both ``EWASH_API_ENABLED`` (Railway convention) and ``API_ENABLED``.
    api_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("EWASH_API_ENABLED", "API_ENABLED"),
    )

    # Rate limits (slowapi syntax — see https://limits.readthedocs.io).
    # 5/hour/phone is generous for a real customer (no one books 6 washes per
    # hour) and tight enough to throttle obvious abuse.
    rate_limit_bookings_per_phone: str = "5/hour"
    rate_limit_bookings_per_ip: str = "20/hour"
    rate_limit_catalog_per_ip: str = "60/minute"
    rate_limit_promo_per_ip: str = "60/hour"
    rate_limit_bookings_list_per_token: str = "60/hour"
    # PWA logout is a single tap; 10/hour absorbs flaky-network double-taps
    # without enabling sustained token-enumeration attempts against the
    # revoke endpoint. Per-token bucket — see rate_limit_token_endpoints_per_ip
    # below for the per-IP umbrella that closes the garbage-token bypass.
    rate_limit_token_revoke_per_token: str = "10/hour"
    # GDPR self-serve erasure (DELETE /me) is once-in-a-customer-lifetime;
    # the very low cap keeps the audit-log table from being spammed.
    rate_limit_me_delete_per_token: str = "3/hour"
    # Per-IP umbrella that token-keyed endpoints (GET /bookings, POST
    # /tokens/revoke, DELETE /me) stack on top of their per-token bucket so
    # an attacker can't rotate garbage X-Ewash-Token values to spawn a fresh
    # bucket per request (ewash-byd). Generous enough that the per-token
    # bucket still bites first for a real customer on a single device but
    # tight enough to cap the aggregate request rate from one origin.
    rate_limit_token_endpoints_per_ip: str = "600/hour"

    # slowapi storage backend. ``memory://`` (default) is in-process and
    # resets on Railway redeploy + does not span workers — fine for a
    # single-process pre-launch deploy. For horizontal scale or to keep
    # caps intact across redeploys, point to a shared store such as
    # ``redis://host:6379/0`` (requires the ``redis`` package; not pinned
    # in requirements.txt — install on demand). See ewash-y0n.
    rate_limit_storage_uri: str = "memory://"
    # slowapi strategy: ``fixed-window`` (default), ``moving-window``, or
    # ``sliding-window-counter``. Moving-window gives smoother caps at the
    # cost of more storage ops per request.
    rate_limit_strategy: str = "fixed-window"

    def allowed_origins_list(self) -> list[str]:
        """Parse ``allowed_origins`` into a clean list of non-empty origins."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
