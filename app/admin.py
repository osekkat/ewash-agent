"""French-first admin portal routes.

This is the v0.3 shell. It is intentionally inert until admin credentials are
configured, so deploying the implementation slice does not expose booking ops.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import time
from datetime import date
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from . import catalog
from .admin_i18n import SUPPORTED_LOCALES, normalize_locale, t
from .catalog import SERVICES_DETAILING, SERVICES_MOTO, SERVICES_WASH
from .cash_ledger import (
    CASH_LEDGER_CATEGORIES,
    cash_summary_for_day,
    expected_cash_balance_minor,
    review_cash_ledger_category,
)
from .config import settings
from .db import session_scope
from .agent_payroll import AGENT_PAYROLL_MANUAL_KEY
from .models import AdminTextRow, AgentPayrollEventRow, CashLedgerEntryRow, OperationalServiceRecordRow
from .operational_tracking import TRACKING_REVIEW_STATUSES, operational_summary_for_day, recent_operational_service_records
from .notifications import get_booking_notification_settings, normalize_phone, upsert_booking_notification_settings
from .persistence import (
    BookingLockBusy,
    admin_booking_list,
    admin_customer_list,
    admin_dashboard_summary,
    anonymize_customer,
    confirm_booking_by_ewash,
    recent_erasures,
    _configured_engine,
)

router = APIRouter(prefix="/admin", tags=["admin"])
log = logging.getLogger("ewash.admin")
_ADMIN_VERSION = "v0.3.0-alpha17"
_SESSION_COOKIE = "ewash_admin_session"
_NAV_ITEMS = (
    ("dashboard", "nav.dashboard", "/admin"),
    ("bookings", "nav.bookings", "/admin/bookings"),
    ("customers", "nav.customers", "/admin/customers"),
    ("b2b", "B2B", "/admin/b2b"),
    ("payroll", "nav.payroll", "/admin/payroll"),
    ("tracking", "nav.tracking", "/admin/tracking"),
    ("cash", "Cash", "/admin/cash"),
    ("personal_finances", "nav.personal_finances", "/admin/personal-finances"),
    ("erasures", "nav.erasures", "/admin/erasures"),
    ("prices", "nav.prices", "/admin/prices"),
    ("promos", "nav.promos", "/admin/promos"),
    ("reminders", "nav.reminders", "/admin/reminders"),
    ("notifications", "nav.notifications", "/admin/notifications"),
    ("closed_dates", "nav.closed_dates", "/admin/closed-dates"),
    ("time_slots", "nav.time_slots", "/admin/time-slots"),
    ("centers", "nav.centers", "/admin/centers"),
    ("copy", "nav.copy", "/admin/copy"),
)
_PAGE_BY_SLUG = {path.rsplit("/", 1)[-1]: (page_id, key, path) for page_id, key, path in _NAV_ITEMS if path != "/admin"}
_CASH_CATEGORY_OPTIONS = (
    "uncategorized",
    "bank",
    "customer_payment",
    "staff_labor",
    "staff_advance",
    "fuel",
    "parking",
    "tolls",
    "supplies",
    "cleaning_products",
    "equipment",
    "vehicle_maintenance",
    "rent_or_site_fee",
    "phone_internet",
    "meals",
    "transport",
    "supplier_payment",
    "owner_draw",
    "other_cash_in",
    "other_cash_out",
)
_PAYROLL_RECEIPT_DIR = Path(__file__).resolve().parents[1] / "documents" / "paie" / "receipts"
_PAYROLL_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_PERSONAL_FINANCE_DIR = Path(__file__).resolve().parents[1] / "documents" / "finances_personnelles"
_PERSONAL_FINANCE_EXTENSIONS = {".xlsx"}
_PERSONAL_FINANCE_TEXT_PREFIX = "personal_finance_workbook:"
_B2B_HERTZ_DIR = Path(__file__).resolve().parents[1] / "documents" / "hertz" / "final"
_B2B_HERTZ_FILE_EXTENSIONS = {".xlsx", ".csv", ".json", ".pdf"}
_B2B_HERTZ_FILE_TEXT_PREFIX = "b2b_hertz_file:"
_B2B_HERTZ_PERIOD_TEXT_PREFIX = "b2b_hertz_period:"
_B2B_HERTZ_CURRENT_PERIOD = "2026-05-21_2026-06-20"


def _session_signature(timestamp: str) -> str:
    return hmac.new(
        settings.admin_password.encode("utf-8"),
        timestamp.encode("utf-8"),
        sha256,
    ).hexdigest()


def _make_session_token() -> str:
    timestamp = str(int(time.time()))
    return f"{timestamp}:{_session_signature(timestamp)}"


def _valid_session_token(token: str | None) -> bool:
    if not settings.admin_password or not token or ":" not in token:
        return False
    timestamp, signature = token.split(":", 1)
    if not timestamp.isdigit():
        return False
    max_age = settings.admin_session_ttl_seconds
    if max_age > 0 and int(time.time()) - int(timestamp) > max_age:
        return False
    return secrets.compare_digest(signature, _session_signature(timestamp))


def _status_label(status_value: str, locale: str) -> str:
    key = f"status.{status_value}"
    label = t(key, locale)
    return status_value if label == key else label


# Source → (emoji, fallback English label, CSS class). The displayed label is
# resolved via t("admin.source.<source>", locale); the tuple's middle slot is
# only used when the i18n lookup misses (defensive — admin_i18n.py declares
# all three keys today).
_SOURCE_BADGES: dict[str, tuple[str, str, str]] = {
    "whatsapp": ("📱", "WhatsApp", "src-wa"),
    "api":      ("🌐", "PWA",      "src-pwa"),
    "admin":    ("👤", "Admin",    "src-admin"),
}


def _source_badge(source: str | None, *, locale: str = "fr") -> str:
    """Return an inline-HTML badge for a booking's `source` value.

    ``source`` may be ``None`` or an unknown string — both fall back to the
    WhatsApp badge so legacy rows persisted before the column existed render
    consistently. The label is i18n'd via ``admin.source.<source>``; the
    badge ``title`` attribute carries the raw source value for hover-debug.

    The returned HTML is safe for direct insertion into the admin template:
    ``source`` is escaped before being placed in attribute context, and the
    localized label is escaped before being placed in text context.
    """
    src = source or "whatsapp"
    icon, fallback_label, css = _SOURCE_BADGES.get(src, _SOURCE_BADGES["whatsapp"])
    if src in _SOURCE_BADGES:
        key = f"admin.source.{src}"
        localized = t(key, locale)
        label = fallback_label if localized == key else localized
    else:
        label = fallback_label
    return (
        f'<span class="badge {css}" title="{escape(src)}">'
        f'{icon} {escape(label)}</span>'
    )


def _phone_display(phone: str | None) -> str:
    """Render a stored phone with a leading ``+`` for the admin UI.

    Phones are stored digits-only (canonicalized via
    :func:`app.notifications.normalize_phone`), so a Moroccan number sits as
    ``212665883062`` in Postgres. The UI re-adds the ``+`` prefix so it reads
    as ``+212665883062``. Anonymized phones (``DEL-…``) and empty values pass
    through untouched — only inputs whose first char is a digit get prefixed.

    Returns HTML-escaped text safe for direct insertion into a template; call
    sites should not wrap the result in ``escape()``.
    """
    raw = phone or ""
    if raw and raw[0].isdigit():
        return escape("+" + raw)
    return escape(raw)


def _language_switch(locale: str) -> str:
    links = []
    for supported in SUPPORTED_LOCALES:
        label = supported.upper()
        if supported == locale:
            links.append(f"<strong>{label}</strong>")
        else:
            links.append(f'<a href="?lang={supported}">{label}</a>')
    return " | ".join(links)


def _layout(*, locale: str, title: str, body: str, active_path: str = "/admin") -> str:
    nav = "".join(
        f'<a href="{escape(path)}" class="active" aria-current="page">{escape(t(key, locale))}</a>'
        if path == active_path else
        f'<a href="{escape(path)}">{escape(t(key, locale))}</a>'
        for _, key, path in _NAV_ITEMS
    )
    return f"""<!doctype html>
<html lang="{escape(locale)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · Ewash Admin</title>
  <style>
    :root {{
      --bg: #08090a;
      --panel: #0f1011;
      --surface: #191a1b;
      --surface-2: #202124;
      --border: rgba(255,255,255,0.08);
      --border-soft: rgba(255,255,255,0.05);
      --text: #f7f8f8;
      --muted: #8a8f98;
      --soft: #d0d6e0;
      --accent: #7170ff;
      --accent-bg: #5e6ad2;
      --good: #10b981;
      --warn: #f59e0b;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(113,112,255,0.16), transparent 34rem),
        linear-gradient(135deg, #08090a 0%, #101114 55%, #08090a 100%);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-feature-settings: "cv01", "ss03";
    }}
    a {{ color: var(--soft); text-decoration: none; }}
    a:hover {{ color: var(--text); }}
    .shell {{ display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }}
    header {{
      padding: 24px 18px;
      background: rgba(15,16,17,0.82);
      border-right: 1px solid var(--border-soft);
      backdrop-filter: blur(12px);
    }}
    .brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 28px; font-weight: 590; letter-spacing: -0.2px; }}
    .brand-mark {{ width: 30px; height: 30px; border-radius: 9px; background: linear-gradient(135deg, var(--accent-bg), #8b5cf6); display: grid; place-items: center; box-shadow: 0 0 30px rgba(113,112,255,0.35); }}
    nav {{ display: grid; gap: 6px; }}
    nav a {{ padding: 9px 10px; border: 1px solid transparent; border-radius: 8px; color: var(--muted); font-size: 14px; font-weight: 510; }}
    nav a.active {{ color: var(--text); background: rgba(255,255,255,0.05); border-color: var(--border); }}
    .lang {{ margin-top: 24px; color: var(--muted); font-size: 13px; }}
    .lang strong, .lang a {{ display: inline-flex; padding: 5px 8px; border: 1px solid var(--border); border-radius: 999px; margin-right: 6px; }}
    .lang strong {{ background: rgba(255,255,255,0.05); color: var(--text); }}
    main {{ padding: 42px; max-width: 1180px; width: 100%; }}
    h1 {{ margin: 0; font-size: clamp(32px, 5vw, 52px); line-height: 1; letter-spacing: -1.05px; font-weight: 510; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: -0.24px; }}
    p {{ color: var(--soft); line-height: 1.6; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 590; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 12px; }}
    .hero {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 28px; }}
    .version-pill {{ border: 1px solid var(--border); background: rgba(255,255,255,0.04); color: var(--soft); border-radius: 999px; padding: 8px 12px; font-size: 13px; white-space: nowrap; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; margin: 28px 0; }}
    .card, .metric-card, .empty-panel {{ background: rgba(255,255,255,0.035); border: 1px solid var(--border); border-radius: 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }}
    .metric-card {{ padding: 18px; }}
    .metric-label {{ color: var(--muted); font-size: 13px; margin-bottom: 12px; }}
    .metric-value {{ font-size: 34px; line-height: 1; font-weight: 510; letter-spacing: -0.7px; }}
    .metric-note {{ color: var(--muted); font-size: 12px; margin-top: 10px; }}
    .dashboard-grid {{ display: grid; grid-template-columns: 1.35fr .85fr; gap: 16px; }}
    .empty-panel {{ padding: 22px; min-height: 230px; }}
    .table-shell {{ margin-top: 18px; border: 1px solid var(--border-soft); border-radius: 12px; overflow: hidden; }}
    .table-row {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; padding: 13px 14px; border-bottom: 1px solid var(--border-soft); color: var(--muted); font-size: 13px; }}
    .booking-row {{ grid-template-columns: .8fr 1.1fr 1fr 1.3fr .9fr .9fr .7fr .85fr; align-items: center; }}
    .customer-row {{ grid-template-columns: 1.1fr .95fr 1.25fr .75fr 1.15fr 1.25fr; align-items: center; }}
    .erasure-row {{ grid-template-columns: 1fr .95fr .75fr .95fr 1.2fr 1.35fr; align-items: center; }}
    .price-row {{ grid-template-columns: .9fr 1.15fr 2fr .55fr .55fr .55fr; align-items: center; }}
    .table-row:last-child {{ border-bottom: 0; }}
    .table-head {{ color: var(--soft); background: rgba(255,255,255,0.03); font-weight: 510; }}
    .status-list {{ display: grid; gap: 10px; margin-top: 18px; }}
    .status-item {{ display: flex; justify-content: space-between; align-items: center; padding: 12px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-soft); color: var(--soft); }}
    .dot {{ width: 8px; height: 8px; border-radius: 999px; background: var(--good); display: inline-block; margin-right: 8px; }}
    .badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 510; line-height: 1; }}
    .badge.src-wa {{ background: #25D366; color: #fff; }}
    .badge.src-pwa {{ background: #7170ff; color: #fff; }}
    .badge.src-admin {{ background: #888; color: #fff; }}
    .soon {{ color: var(--muted); font-size: 13px; }}
    form {{ max-width: 420px; margin-top: 24px; padding: 22px; border: 1px solid var(--border); border-radius: 16px; background: rgba(255,255,255,0.035); }}
    label {{ color: var(--soft); font-size: 14px; }}
    input, textarea, select {{ width: 100%; margin: 8px 0 14px; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--border); color: var(--text); background: rgba(255,255,255,0.04); }}
    textarea {{ min-height: 110px; resize: vertical; }}
    input[type="checkbox"] {{ width: auto; margin-right: 8px; }}
    .admin-form {{ max-width: none; }}
    .inline-form {{ max-width: none; margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; }}
    .inline-form button {{ padding: 8px 10px; font-size: 12px; white-space: nowrap; }}
    .erase-form {{ display: grid; gap: 8px; }}
    .erase-form input, .erase-form textarea {{ margin: 0; padding: 8px 9px; font-size: 12px; }}
    .erase-form textarea {{ min-height: 54px; }}
    .price-input {{ margin: 0; padding: 9px 10px; min-width: 70px; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .notice {{ padding: 12px 14px; border-radius: 12px; margin: 14px 0; border: 1px solid var(--border); }}
    .notice.ok {{ color: #bbf7d0; background: rgba(16,185,129,0.08); }}
    .notice.error {{ color: #fecaca; background: rgba(239,68,68,0.08); }}
    .muted {{ color: var(--muted); }}
    button {{ border: 0; border-radius: 10px; padding: 11px 16px; color: #fff; background: var(--accent-bg); font-weight: 590; cursor: pointer; }}
    button.danger {{ background: var(--danger); }}
    [role="alert"] {{ color: #fecaca; }}
    @media (max-width: 860px) {{
      .shell {{ grid-template-columns: 1fr; }}
      header {{ border-right: 0; border-bottom: 1px solid var(--border-soft); }}
      nav {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
      main {{ padding: 28px 18px; }}
      .hero, .dashboard-grid {{ display: block; }}
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
      .empty-panel {{ margin-top: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand"><span class="brand-mark">E</span><span>Ewash Admin</span></div>
      <nav aria-label="Admin navigation">{nav}</nav>
      <p class="lang">{_language_switch(locale)}</p>
    </header>
    <main>{body}</main>
  </div>
</body>
</html>"""


def _password_form(*, locale: str, error: str = "") -> HTMLResponse:
    title = t("admin.password.title", locale)
    error_html = f'<p role="alert"><strong>{escape(error)}</strong></p>' if error else ""
    body = f"""
<h1>{escape(title)}</h1>
{error_html}
<form method="post" action="/admin?lang={escape(locale)}">
  <label for="password">{escape(t('admin.password.label', locale))}</label><br>
  <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
  <button type="submit">{escape(t('admin.password.submit', locale))}</button>
</form>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body), status_code=200)


def _dashboard(*, locale: str) -> HTMLResponse:
    title = t("nav.dashboard", locale)
    summary = admin_dashboard_summary()
    if summary.recent_bookings:
        recent_rows = "".join(
            "<div class=\"table-row\">"
            f'<span>{escape(item.customer_name)} {_source_badge(item.source, locale=locale)}</span>'
            f"<span>{escape(item.service_label)}</span>"
            f"<span>{escape(_status_label(item.status, locale))}</span>"
            "</div>"
            for item in summary.recent_bookings
        )
        recent_text = t("admin.panel.recent_bookings_intro", locale)
    else:
        recent_rows = '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
        recent_text = t("admin.panel.no_bookings", locale)

    persistence_state = "OK" if summary.db_available else escape(t('admin.next.soon', locale))
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(t('admin.dashboard.placeholder', locale))}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>

<section class="metric-grid" aria-label="Résumé opérationnel">
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.bookings_today', locale))}</div>
    <div class="metric-value">{summary.total_bookings}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.awaiting_confirmation', locale))}</div>
    <div class="metric-value">{summary.awaiting_confirmation}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.pending_ewash_confirmation', locale))}</div>
    <div class="metric-value">{summary.pending_ewash_confirmation}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.customers', locale))}</div>
    <div class="metric-value">{summary.customers}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.dropped_leads', locale))}</div>
    <div class="metric-value">{summary.abandoned_conversations}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.reminders', locale))}</div>
    <div class="metric-value">{summary.pending_reminders}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label"><span class="badge src-pwa">PWA</span> {escape(t('admin.dashboard.bookings_pwa_7d', locale))}</div>
    <div class="metric-value">{summary.bookings_pwa_last_7d}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label"><span class="badge src-wa">WhatsApp</span> {escape(t('admin.dashboard.bookings_whatsapp_7d', locale))}</div>
    <div class="metric-value">{summary.bookings_whatsapp_last_7d}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
</section>

<section class="dashboard-grid">
  <article class="empty-panel">
    <h2>{escape(t('admin.panel.recent_bookings', locale))}</h2>
    <p>{escape(recent_text)}</p>
    <div class="table-shell" aria-label="Réservations récentes">
      <div class="table-row table-head"><span>Client</span><span>Service</span><span>Statut</span></div>
      {recent_rows}
    </div>
  </article>
  <aside class="empty-panel">
    <h2>{escape(t('admin.panel.next_steps', locale))}</h2>
    <div class="status-list">
      <div class="status-item"><span><span class="dot"></span>{escape(t('admin.next.password', locale))}</span><span>OK</span></div>
      <div class="status-item"><span><span class="dot"></span>{escape(t('admin.next.db', locale))}</span><span>OK</span></div>
      <div class="status-item"><span><span class="dot"></span>{escape(t('admin.next.persistence', locale))}</span><span>{persistence_state}</span></div>
      <div class="status-item"><span><span class="dot"></span>{escape(t('admin.next.pages', locale))}</span><span>OK</span></div>
    </div>
    <p><a href="/admin/logout">{escape(t('nav.logout', locale))}</a></p>
  </aside>
</section>
"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body), status_code=200)


def _notice_html(*, message: str = "", error: str = "") -> str:
    if error:
        return f'<div class="notice error" role="alert">{escape(error)}</div>'
    if message:
        return f'<div class="notice ok">{escape(message)}</div>'
    return ""


def _parse_int_field(value: str, *, label: str) -> int:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{label}: prix manquant")
    try:
        price = int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{label}: prix invalide") from exc
    if price < 0 or price > 100_000:
        raise ValueError(f"{label}: prix hors limites")
    return price


def _price_input_name(service_id: str, category: str) -> str:
    return f"price__{service_id}__{category}"


def _discount_input_name(service_id: str, category: str) -> str:
    return f"discount__{service_id}__{category}"


def _price_cell(service_id: str, category: str) -> str:
    price = catalog.public_service_price(service_id, category)
    value = "" if price is None else str(price)
    return (
        f'<input class="price-input" type="number" min="0" step="1" '
        f'name="{escape(_price_input_name(service_id, category))}" value="{escape(value)}">'
        f'<small class="muted">{escape(value)} DH</small>'
    )


def _price_rows(*, locale: str) -> str:
    rows: list[str] = []
    for group_label, services in (
        (t("admin.prices.washes", locale), SERVICES_WASH),
        (t("admin.prices.detailing", locale), SERVICES_DETAILING),
    ):
        for _sid, name, desc, prices in services:
            rows.append(
                "<div class=\"table-row price-row\">"
                f"<span>{escape(group_label)}</span>"
                f"<span>{escape(name)}</span>"
                f"<span>{escape(desc)}</span>"
                f"<span>{_price_cell(_sid, 'A')}</span>"
                f"<span>{_price_cell(_sid, 'B')}</span>"
                f"<span>{_price_cell(_sid, 'C')}</span>"
                "</div>"
            )
    for _sid, name, desc, price in SERVICES_MOTO:
        rows.append(
            "<div class=\"table-row price-row\">"
            f"<span>{escape(t('admin.prices.moto', locale))}</span>"
            f"<span>{escape(name)}</span>"
            f"<span>{escape(desc)}</span>"
            f"<span>{_price_cell(_sid, catalog.MOTO_PRICE_CATEGORY)}</span>"
            "<span>—</span>"
            "<span>—</span>"
            "</div>"
        )
    return "".join(rows)


def _prices_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.prices", locale)
    intro = t("admin.prices.intro", locale)
    notice = _notice_html(message=message, error=error)
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="empty-panel">
  <h2>{escape(t('admin.prices.public_tariff', locale))}</h2>
  {notice}
  <form class="admin-form" method="post" action="/admin/prices?lang={escape(locale)}">
  <div class="table-shell" aria-label="{escape(t('admin.prices.public_tariff', locale))}">
    <div class="table-row table-head price-row"><span>{escape(t('admin.prices.group', locale))}</span><span>{escape(t('admin.prices.service', locale))}</span><span>{escape(t('admin.prices.description', locale))}</span><span>A</span><span>B</span><span>C</span></div>
    {_price_rows(locale=locale)}
  </div>
  <p><button type="submit">{escape(t('action.save', locale))}</button></p>
  </form>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/prices"),
        status_code=200,
    )


def _promo_discount_cell(service_id: str, category: str, discounts: dict[tuple[str, str], int] | None = None) -> str:
    discounts = discounts or {}
    value = discounts.get((service_id, category))
    public = catalog.public_service_price(service_id, category)
    display_value = value if value is not None else public
    display = "" if display_value is None else str(display_value)
    placeholder = "" if public is None else str(public)
    discount_label = f"{display} DH" if display else "—"
    public_attr = "" if public is None else f' data-public-price="{escape(str(public))}"'
    return (
        f'<input class="price-input" type="number" min="0" step="1" '
        f'name="{escape(_discount_input_name(service_id, category))}" value="{escape(display)}" '
        f'placeholder="{escape(placeholder)}"{public_attr}>'
        f'<small class="muted">{escape(discount_label)}</small>'
    )


def _promo_discount_rows(discounts: dict[tuple[str, str], int] | None = None) -> str:
    rows: list[str] = []
    for group_label, services in (("Lavages", SERVICES_WASH), ("Esthétique", SERVICES_DETAILING)):
        for service_id, name, desc, _prices in services:
            rows.append(
                "<div class=\"table-row price-row\">"
                f"<span>{escape(group_label)}</span>"
                f"<span>{escape(name)}</span>"
                f"<span>{escape(desc)}</span>"
                f"<span>{_promo_discount_cell(service_id, 'A', discounts)}</span>"
                f"<span>{_promo_discount_cell(service_id, 'B', discounts)}</span>"
                f"<span>{_promo_discount_cell(service_id, 'C', discounts)}</span>"
                "</div>"
            )
    return "".join(rows)


def _promo_list_rows(locale: str) -> str:
    promos = catalog.list_promo_codes()
    if not promos:
        return '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    rows: list[str] = []
    for promo in promos:
        active = t("admin.promos.active", locale) if promo.active else t("admin.promos.inactive", locale)
        discount_parts = []
        for (service_id, category), price in sorted(promo.discounts.items()):
            discount_parts.append(f"{catalog.service_name(service_id)} {category}: {price} DH")
        discount_text = ", ".join(discount_parts[:8])
        if len(discount_parts) > 8:
            discount_text += f" … +{len(discount_parts) - 8}"
        rows.append(
            "<div class=\"table-row\">"
            f"<span>{escape(promo.code)}<br><small>{escape(active)}</small></span>"
            f"<span>{escape(promo.label)}</span>"
            f"<span>{escape(discount_text or '—')}</span>"
            "</div>"
        )
    return "".join(rows)


def _promos_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.promos", locale)
    notice = _notice_html(message=message, error=error)
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(t('admin.promos.intro', locale))}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="dashboard-grid">
  <article class="empty-panel">
    <h2>{escape(t('admin.promos.existing', locale))}</h2>
    {notice}
    <div class="table-shell" aria-label="{escape(t('admin.promos.existing', locale))}">
      <div class="table-row table-head"><span>{escape(t('admin.promos.code', locale))}</span><span>{escape(t('admin.promos.label', locale))}</span><span>{escape(t('admin.promos.discounts', locale))}</span></div>
      {_promo_list_rows(locale)}
    </div>
  </article>
  <aside class="empty-panel">
    <h2>{escape(t('admin.promos.add_or_update', locale))}</h2>
    <form class="admin-form" method="post" action="/admin/promos?lang={escape(locale)}">
      <div class="form-grid">
        <label>{escape(t('admin.promos.code', locale))}<input name="code" placeholder="VIP30" required></label>
        <label>{escape(t('admin.promos.label', locale))}<input name="label" placeholder="VIP Thirty" required></label>
      </div>
      <label><input type="checkbox" name="active" checked>{escape(t('admin.promos.active', locale))}</label>
      <p class="muted">{escape(t('admin.promos.discount_help', locale))}</p>
      <div class="table-shell" aria-label="{escape(t('admin.promos.discounts', locale))}">
        <div class="table-row table-head price-row"><span>{escape(t('admin.prices.group', locale))}</span><span>{escape(t('admin.prices.service', locale))}</span><span>{escape(t('admin.prices.description', locale))}</span><span>A</span><span>B</span><span>C</span></div>
        {_promo_discount_rows()}
      </div>
      <p><button type="submit">{escape(t('action.save', locale))}</button></p>
    </form>
  </aside>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/promos"),
        status_code=200,
    )


def _simple_status(active: bool, locale: str) -> str:
    return t("admin.promos.active", locale) if active else t("admin.promos.inactive", locale)


def _reminders_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.reminders", locale)
    rows = "".join(
        "<div class=\"table-row\">"
        f"<span>{escape(rule.name)}<br><small>{escape(_simple_status(rule.enabled, locale))}</small></span>"
        f"<span>{rule.offset_minutes_before} min</span>"
        f"<span>{escape(rule.template_name or '—')}</span>"
        "</div>"
        for rule in catalog.list_reminder_rules()
    ) or '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.reminders.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid">
  <article class="empty-panel"><h2>{escape(t('admin.reminders.rules', locale))}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>{escape(t('admin.reminders.name', locale))}</span><span>{escape(t('admin.reminders.offset', locale))}</span><span>{escape(t('admin.reminders.template', locale))}</span></div>{rows}</div></article>
  <aside class="empty-panel"><h2>{escape(t('admin.reminders.add_or_update', locale))}</h2><form class="admin-form" method="post" action="/admin/reminders?lang={escape(locale)}"><label>{escape(t('admin.reminders.name', locale))}<input name="reminder_name" placeholder="H-1" required></label><label>{escape(t('admin.reminders.offset', locale))}<input type="number" min="1" step="1" name="offset_minutes_before" value="60" required></label><label>{escape(t('admin.reminders.template', locale))}<input name="template_name" placeholder="booking_reminder_h1"></label><label><input type="checkbox" name="enabled" checked>{escape(t('admin.promos.active', locale))}</label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside>
</section>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/reminders"), status_code=200)


def _notifications_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.notifications", locale)
    config = get_booking_notification_settings()
    checked = " checked" if config.enabled else ""
    status_label = _simple_status(config.enabled, locale)
    phone_value = escape(config.phone_number)
    template_value = escape(config.template_name)
    language_value = escape(config.template_language or "fr")
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.notifications.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid">
  <article class="empty-panel"><h2>{escape(t('admin.notifications.current', locale))}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>{escape(t('admin.notifications.enabled', locale))}</span><span>{escape(t('admin.notifications.phone', locale))}</span><span>{escape(t('admin.notifications.template', locale))}</span></div><div class="table-row"><span>{escape(status_label)}</span><span>{_phone_display(config.phone_number) or '—'}</span><span>{template_value or '—'}<br><small>{language_value}</small></span></div></div><h2 style="margin-top:18px">{escape(t('admin.notifications.template_contract', locale))}</h2><p>{escape(t('admin.notifications.template_contract_body', locale))}</p></article>
  <aside class="empty-panel"><h2>{escape(t('admin.notifications.form_title', locale))}</h2><form class="admin-form" method="post" action="/admin/notifications?lang={escape(locale)}"><label><input type="checkbox" name="enabled"{checked}>{escape(t('admin.notifications.enabled', locale))}</label><label>{escape(t('admin.notifications.phone', locale))}<input name="phone_number" inputmode="tel" placeholder="+212665883062" value="{phone_value}"></label><label>{escape(t('admin.notifications.template', locale))}<input name="template_name" placeholder="new_booking_alert" value="{template_value}"></label><label>{escape(t('admin.notifications.language', locale))}<input name="template_language" placeholder="fr" value="{language_value}"></label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside>
</section>"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/notifications"),
        status_code=200,
    )


def _closed_dates_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.closed_dates", locale)
    rows = "".join(
        "<div class=\"table-row\">"
        f"<span>{escape(item.date_iso)}<br><small>{escape(_simple_status(item.active, locale))}</small></span>"
        f"<span>{escape(item.label or '—')}</span><span>—</span></div>"
        for item in catalog.list_closed_dates()
    ) or '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.closed_dates.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid"><article class="empty-panel"><h2>{escape(title)}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>{escape(t('admin.closed_dates.date', locale))}</span><span>{escape(t('admin.closed_dates.label', locale))}</span><span></span></div>{rows}</div></article>
<aside class="empty-panel"><h2>{escape(t('admin.closed_dates.add_or_update', locale))}</h2><form class="admin-form" method="post" action="/admin/closed-dates?lang={escape(locale)}"><label>{escape(t('admin.closed_dates.date', locale))}<input type="date" name="closed_date" required></label><label>{escape(t('admin.closed_dates.label', locale))}<input name="label" placeholder="Eid / Maintenance"></label><label><input type="checkbox" name="active" checked>{escape(t('admin.promos.active', locale))}</label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside></section>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/closed-dates"), status_code=200)


def _time_slots_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.time_slots", locale)
    rows = "".join(
        "<div class=\"table-row\">"
        f"<span>{escape(item.slot_id)}<br><small>{escape(_simple_status(item.active, locale))}</small></span>"
        f"<span>{escape(item.label)}</span><span>{escape(item.period or '—')}</span></div>"
        for item in catalog.list_time_slots()
    ) or '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.time_slots.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid"><article class="empty-panel"><h2>{escape(title)}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>ID</span><span>{escape(t('admin.time_slots.label', locale))}</span><span>{escape(t('admin.time_slots.period', locale))}</span></div>{rows}</div></article>
<aside class="empty-panel"><h2>{escape(t('admin.time_slots.add_or_update', locale))}</h2><form class="admin-form" method="post" action="/admin/time-slots?lang={escape(locale)}"><label>ID<input name="slot_id" placeholder="slot_22_23" required></label><label>{escape(t('admin.time_slots.label', locale))}<input name="label" placeholder="22h – 23h" required></label><label>{escape(t('admin.time_slots.period', locale))}<input name="period" placeholder="Soirée"></label><label><input type="checkbox" name="active" checked>{escape(t('admin.promos.active', locale))}</label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside></section>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/time-slots"), status_code=200)


def _centers_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.centers", locale)
    rows = "".join(
        "<div class=\"table-row\">"
        f"<span>{escape(item.center_id)}<br><small>{escape(_simple_status(item.active, locale))}</small></span>"
        f"<span>{escape(item.name)}</span><span>{escape(item.details or '—')}</span></div>"
        for item in catalog.list_centers()
    ) or '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.centers.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid"><article class="empty-panel"><h2>{escape(title)}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>ID</span><span>{escape(t('admin.centers.name', locale))}</span><span>{escape(t('admin.centers.details', locale))}</span></div>{rows}</div></article>
<aside class="empty-panel"><h2>{escape(t('admin.centers.add_or_update', locale))}</h2><form class="admin-form" method="post" action="/admin/centers?lang={escape(locale)}"><label>ID<input name="center_id" placeholder="ctr_casa" required></label><label>{escape(t('admin.centers.name', locale))}<input name="name" placeholder="Stand physique" required></label><label>{escape(t('admin.centers.details', locale))}<textarea name="details" placeholder="Mall Triangle Vert, Bouskoura · 7j/7 · 09h-22h30"></textarea></label><label><input type="checkbox" name="active" checked>{escape(t('admin.promos.active', locale))}</label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside></section>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/centers"), status_code=200)


def _copy_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.copy", locale)
    rows = "".join(
        "<div class=\"table-row\">"
        f"<span>{escape(item.key)}<br><small>{escape(item.title)}</small></span>"
        f"<span>{escape(item.body)}</span><span>—</span></div>"
        for item in catalog.list_text_snippets()
    ) or '<div class="table-row"><span>—</span><span>—</span><span>—</span></div>'
    body = f"""
<section class="hero"><div><div class="eyebrow">Ewash Ops</div><h1>{escape(title)}</h1><p>{escape(t('admin.copy.intro', locale))}</p></div><div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div></section>

<section class="dashboard-grid"><article class="empty-panel"><h2>{escape(title)}</h2>{_notice_html(message=message, error=error)}<div class="table-shell"><div class="table-row table-head"><span>{escape(t('admin.copy.key', locale))}</span><span>{escape(t('admin.copy.body', locale))}</span><span></span></div>{rows}</div></article>
<aside class="empty-panel"><h2>{escape(t('admin.copy.add_or_update', locale))}</h2><form class="admin-form" method="post" action="/admin/copy?lang={escape(locale)}"><label>{escape(t('admin.copy.key', locale))}<input name="text_key" placeholder="booking.welcome" required></label><label>{escape(t('admin.copy.title', locale))}<input name="title" placeholder="Accueil réservation" required></label><label>{escape(t('admin.copy.body', locale))}<textarea name="body" required></textarea></label><p><button type="submit">{escape(t('action.save', locale))}</button></p></form></aside></section>"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/copy"), status_code=200)


def _agent_payroll_raw_payload(row: AgentPayrollEventRow) -> dict[str, object]:
    try:
        payload = json.loads(row.raw_payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _payroll_receipt_filename(row: AgentPayrollEventRow) -> str:
    payload = _agent_payroll_raw_payload(row)
    receipt = payload.get("receipt_filename") or payload.get("receipt_file") or payload.get("receipt_path") or ""
    filename = str(receipt).replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or filename in {".", ".."} or Path(filename).name != filename:
        return ""
    if Path(filename).suffix.lower() not in _PAYROLL_RECEIPT_EXTENSIONS:
        return ""
    return filename


def _payroll_receipt_link(row: AgentPayrollEventRow) -> str:
    payload = _agent_payroll_raw_payload(row)
    if payload.get("receipt_image_base64"):
        href = f"/admin/payroll/receipts/event/{row.id}"
        return f'<a href="{href}" target="_blank" rel="noopener">Ticket compta</a>'
    filename = _payroll_receipt_filename(row)
    if not filename:
        return "—"
    href = f"/admin/payroll/receipts/{quote(filename, safe='')}"
    return f'<a href="{href}" target="_blank" rel="noopener">Ticket compta</a>'


def _payroll_page(*, locale: str) -> HTMLResponse:
    title = t("nav.payroll", locale)
    engine = _configured_engine()
    if engine is None:
        body = f"""
        <div class="hero"><div><div class="eyebrow">Payroll</div><h1>{escape(title)}</h1></div></div>
        <section class="empty-panel"><h2>Not configured</h2><p>Set DATABASE_URL to enable agent payroll tracking.</p></section>
        """
        return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/payroll"))

    with session_scope(engine) as session:
        rows = session.scalars(
            select(AgentPayrollEventRow)
            .order_by(AgentPayrollEventRow.event_date.desc(), AgentPayrollEventRow.id.asc())
            .limit(200)
        ).all()
        manual = session.get(AdminTextRow, AGENT_PAYROLL_MANUAL_KEY)

    total_hours = sum(float(row.impacted_hours or 0) for row in rows)
    total_impact = sum(int(row.payroll_impact_dh or 0) for row in rows)
    pending_count = sum(1 for row in rows if row.status == "À vérifier")
    payroll_grid = ".75fr 1fr 1.2fr .65fr .65fr .8fr 1.55fr .75fr .8fr"
    table_rows = "".join(
        f"<div class='table-row' style='grid-template-columns:{payroll_grid};'>"
        f"<span>{escape(row.event_date.isoformat() if row.event_date else '')}</span>"
        f"<span>{escape(row.agent_name)}</span>"
        f"<span>{escape(row.event_type)}</span>"
        f"<span>{escape(row.warned or '—')}</span>"
        f"<span>{escape(str(row.impacted_hours) if row.impacted_hours is not None else '—')}</span>"
        f"<span>{escape(str(row.payroll_impact_dh) + ' MAD' if row.payroll_impact_dh is not None else '—')}</span>"
        f"<span>{escape(row.comment or '—')}</span>"
        f"<span>{_payroll_receipt_link(row)}</span>"
        f"<span>{escape(row.status)}</span>"
        "</div>"
        for row in rows
    ) or "<div class='table-row'><span>Aucun événement agent importé.</span><span></span><span></span></div>"
    manual_body = escape(manual.body if manual else "Aucun mode d’emploi importé.").replace("\n", "<br>")

    body = f"""
    <div class="hero"><div><div class="eyebrow">Agents</div><h1>{escape(title)}</h1><p>Suivi des absences, retards, incidents et impacts paie importés depuis les anciens fichiers.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Événements importés</div><div class="metric-value">{len(rows)}</div></div>
      <div class="metric-card"><div class="metric-label">À vérifier</div><div class="metric-value">{pending_count}</div></div>
      <div class="metric-card"><div class="metric-label">Heures impactées</div><div class="metric-value">{total_hours:g}</div></div>
      <div class="metric-card"><div class="metric-label">Impact paie saisi</div><div class="metric-value">{total_impact} MAD</div></div>
    </section>
    <section class="card" style="padding:18px; margin-bottom:18px;">
      <h2>{escape(manual.title if manual else 'Mode d’emploi')}</h2>
      <p>{manual_body}</p>
    </section>
    <section class="card" style="padding:18px;">
      <h2>Événements agents</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:{payroll_grid};"><span>Date</span><span>Agent</span><span>Type</span><span>Prévenu</span><span>Heures</span><span>Impact</span><span>Commentaire</span><span>Ticket</span><span>Statut</span></div>
        {table_rows}
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/payroll"))


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} o"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} Ko"
    return f"{size_bytes / (1024 * 1024):.1f} Mo"


def _safe_personal_finance_filename(filename: str) -> str:
    safe_name = filename.replace("\\", "/")
    if "/" in safe_name or safe_name in {"", ".", ".."}:
        return ""
    if Path(safe_name).name != safe_name:
        return ""
    if Path(safe_name).suffix.lower() not in _PERSONAL_FINANCE_EXTENSIONS:
        return ""
    return safe_name


def _personal_finance_payload(row: AdminTextRow) -> dict[str, object]:
    try:
        payload = json.loads(row.body or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _personal_finance_db_records() -> list[dict[str, object]]:
    engine = _configured_engine()
    if engine is None:
        return []
    with session_scope(engine) as session:
        rows = session.scalars(
            select(AdminTextRow)
            .where(AdminTextRow.text_key.like(f"{_PERSONAL_FINANCE_TEXT_PREFIX}%"))
            .order_by(AdminTextRow.text_key.desc())
        ).all()
    records: list[dict[str, object]] = []
    for row in rows:
        payload = _personal_finance_payload(row)
        fallback_name = row.text_key.removeprefix(_PERSONAL_FINANCE_TEXT_PREFIX)
        filename = _safe_personal_finance_filename(str(payload.get("filename") or fallback_name))
        if not filename:
            continue
        try:
            size_bytes = int(str(payload.get("size_bytes") or "0"))
        except ValueError:
            size_bytes = 0
        records.append(
            {
                "filename": filename,
                "modified": row.updated_at.strftime("%Y-%m-%d %H:%M") if row.updated_at else "—",
                "size": _format_file_size(size_bytes) if size_bytes else "—",
                "href": f"/admin/personal-finances/files/{quote(filename, safe='')}",
            }
        )
    return records


def _personal_finance_filesystem_records() -> list[dict[str, object]]:
    if not _PERSONAL_FINANCE_DIR.exists():
        return []
    records = []
    for path in sorted(_PERSONAL_FINANCE_DIR.iterdir(), key=lambda item: item.name, reverse=True):
        if not path.is_file() or path.suffix.lower() not in _PERSONAL_FINANCE_EXTENSIONS:
            continue
        records.append(
            {
                "filename": path.name,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
                "size": _format_file_size(path.stat().st_size),
                "href": f"/admin/personal-finances/files/{quote(path.name, safe='')}",
            }
        )
    return records


def _personal_finance_records() -> list[dict[str, object]]:
    records = _personal_finance_db_records()
    seen = {str(record["filename"]) for record in records}
    for record in _personal_finance_filesystem_records():
        if str(record["filename"]) in seen:
            continue
        records.append(record)
    return records


def _personal_finances_page(*, locale: str) -> HTMLResponse:
    title = t("nav.personal_finances", locale)
    files = _personal_finance_records()
    table_rows = "".join(
        "<div class='table-row' style='grid-template-columns:1.4fr .75fr .75fr .6fr;'>"
        f"<span>{escape(str(record['filename']))}</span>"
        f"<span>{escape(str(record['modified']))}</span>"
        f"<span>{escape(str(record['size']))}</span>"
        f"<span><a href=\"{escape(str(record['href']))}\">Télécharger</a></span>"
        "</div>"
        for record in files
    ) or "<div class='table-row'><span>Aucun fichier de finances personnelles pour le moment.</span><span></span><span></span><span></span></div>"
    body = f"""
    <div class="hero"><div><div class="eyebrow">Personnel</div><h1>{escape(title)}</h1><p>Dépenses personnelles et rentrées d’argent personnelles d’Omar, alimentées progressivement depuis WhatsApp.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Fichiers mensuels</div><div class="metric-value">{len(files)}</div></div>
    </section>
    <section class="card" style="padding:18px;">
      <h2>Fichiers disponibles</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:1.4fr .75fr .75fr .6fr;"><span>Fichier</span><span>Modifié</span><span>Taille</span><span>Action</span></div>
        {table_rows}
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/personal-finances"))


def _safe_b2b_hertz_filename(filename: str) -> str:
    safe_name = filename.replace("\\", "/").strip()
    if "/" in safe_name or safe_name in {"", ".", ".."}:
        return ""
    if Path(safe_name).name != safe_name:
        return ""
    if Path(safe_name).suffix.lower() not in _B2B_HERTZ_FILE_EXTENSIONS:
        return ""
    return safe_name


def _json_payload(row: AdminTextRow | None) -> dict[str, object]:
    if row is None:
        return {}
    try:
        payload = json.loads(row.body or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _b2b_hertz_db_file_records() -> list[dict[str, object]]:
    engine = _configured_engine()
    if engine is None:
        return []
    with session_scope(engine) as session:
        rows = session.scalars(
            select(AdminTextRow)
            .where(AdminTextRow.text_key.like(f"{_B2B_HERTZ_FILE_TEXT_PREFIX}%"))
            .order_by(AdminTextRow.text_key.asc())
        ).all()
    records: list[dict[str, object]] = []
    for row in rows:
        payload = _json_payload(row)
        fallback_name = row.text_key.removeprefix(_B2B_HERTZ_FILE_TEXT_PREFIX)
        filename = _safe_b2b_hertz_filename(str(payload.get("filename") or fallback_name))
        if not filename:
            continue
        try:
            size_bytes = int(str(payload.get("size_bytes") or "0"))
        except ValueError:
            size_bytes = 0
        label = str(payload.get("label") or filename)
        records.append(
            {
                "filename": filename,
                "label": label,
                "modified": row.updated_at.strftime("%Y-%m-%d %H:%M") if row.updated_at else "—",
                "size": _format_file_size(size_bytes) if size_bytes else "—",
                "href": f"/admin/b2b/hertz/files/{quote(filename, safe='')}",
            }
        )
    return records


def _b2b_hertz_filesystem_records() -> list[dict[str, object]]:
    if not _B2B_HERTZ_DIR.exists():
        return []
    records = []
    for path in sorted(_B2B_HERTZ_DIR.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.suffix.lower() not in _B2B_HERTZ_FILE_EXTENSIONS:
            continue
        records.append(
            {
                "filename": path.name,
                "label": path.name,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
                "size": _format_file_size(path.stat().st_size),
                "href": f"/admin/b2b/hertz/files/{quote(path.name, safe='')}",
            }
        )
    return records


def _b2b_hertz_file_records() -> list[dict[str, object]]:
    records = _b2b_hertz_db_file_records()
    seen = {str(record["filename"]) for record in records}
    for record in _b2b_hertz_filesystem_records():
        if str(record["filename"]) in seen:
            continue
        records.append(record)
    return records


def _b2b_hertz_period_payload(period: str = _B2B_HERTZ_CURRENT_PERIOD) -> dict[str, object]:
    engine = _configured_engine()
    if engine is not None:
        with session_scope(engine) as session:
            row = session.get(AdminTextRow, f"{_B2B_HERTZ_PERIOD_TEXT_PREFIX}{period}")
            payload = _json_payload(row)
        if payload:
            return payload
    path = _B2B_HERTZ_DIR / f"hertz_b2b_{period}.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _dh_major(amount: int) -> str:
    return f"{int(amount or 0):,} MAD".replace(",", " ")


def _b2b_page(*, locale: str) -> HTMLResponse:
    title = "B2B"
    hertz_payload = _b2b_hertz_period_payload()
    summary = hertz_payload.get("summary") if isinstance(hertz_payload.get("summary"), dict) else {}
    total = int(summary.get("total_vehicles") or 0) if isinstance(summary, dict) else 0
    total_ht = int(summary.get("total_ht") or 0) if isinstance(summary, dict) else 0
    body = f"""
    <div class="hero"><div><div class="eyebrow">Clients entreprises</div><h1>B2B</h1><p>Suivi des clients B2B eWash : périodes, prestations, fichiers et factures.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Clients actifs</div><div class="metric-value">1</div></div>
      <div class="metric-card"><div class="metric-label">Véhicules Hertz période en cours</div><div class="metric-value">{total}</div></div>
      <div class="metric-card"><div class="metric-label">Total Hertz HT</div><div class="metric-value">{_dh_major(total_ht)}</div></div>
    </section>
    <section class="card" style="padding:18px;">
      <h2>Clients B2B</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:1fr .7fr .7fr .6fr;"><span>Client</span><span>Période</span><span>Suivi</span><span>Action</span></div>
        <div class="table-row" style="grid-template-columns:1fr .7fr .7fr .6fr;"><span>Hertz</span><span>21/05/2026 → 20/06/2026</span><span>{total} véhicules</span><span><a href="/admin/b2b/hertz">Ouvrir</a></span></div>
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/b2b"))


def _b2b_hertz_page(*, locale: str) -> HTMLResponse:
    title = "B2B · Hertz"
    payload = _b2b_hertz_period_payload()
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    raw_rows = payload.get("rows")
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    files = _b2b_hertz_file_records()
    total_vehicles = int(summary.get("total_vehicles") or len(rows)) if isinstance(summary, dict) else len(rows)
    total_ht = int(summary.get("total_ht") or total_vehicles * 50) if isinstance(summary, dict) else total_vehicles * 50
    tva = int(summary.get("tva") or total_ht * 0.2) if isinstance(summary, dict) else int(total_ht * 0.2)
    ttc = int(summary.get("ttc") or total_ht + tva) if isinstance(summary, dict) else total_ht + tva
    invoice_number = escape(str(summary.get("invoice_number") or "EW-B2B-HERTZ-2026-0001")) if isinstance(summary, dict) else "EW-B2B-HERTZ-2026-0001"
    by_site = summary.get("by_site") if isinstance(summary.get("by_site"), dict) else {}
    casa_count = int(by_site.get("Casa Aéroport") or 0) if isinstance(by_site, dict) else 0
    fes_count = int(by_site.get("Fès Aéroport") or 0) if isinstance(by_site, dict) else 0
    table_rows = "".join(
        "<div class='table-row' style='grid-template-columns:.55fr .75fr 1.25fr .8fr .45fr .7fr;'>"
        f"<span>{escape(str(row.get('date') or ''))}</span>"
        f"<span>{escape(str(row.get('site') or ''))}</span>"
        f"<span>{escape(str(row.get('vehicle') or ''))}</span>"
        f"<span>{escape(str(row.get('matricule') or ''))}</span>"
        f"<span>{escape(str(row.get('tarif_ht') or 50))}</span>"
        f"<span>{escape(str(row.get('notes') or ''))}</span>"
        "</div>"
        for row in rows[:350]
        if isinstance(row, dict)
    ) or "<div class='table-row'><span>Aucune ligne Hertz chargée pour le moment.</span><span></span><span></span><span></span><span></span><span></span></div>"
    file_rows = "".join(
        "<div class='table-row' style='grid-template-columns:1.2fr .6fr .5fr .45fr;'>"
        f"<span>{escape(str(record['label']))}</span>"
        f"<span>{escape(str(record['modified']))}</span>"
        f"<span>{escape(str(record['size']))}</span>"
        f"<span><a href=\"{escape(str(record['href']))}\">Télécharger</a></span>"
        "</div>"
        for record in files
    ) or "<div class='table-row'><span>Aucun fichier disponible.</span><span></span><span></span><span></span></div>"
    body = f"""
    <div class="hero"><div><div class="eyebrow">B2B · Hertz</div><h1>Suivi Hertz</h1><p>Consultation du suivi véhicules, fichiers et facture de la période 21/05/2026 → 20/06/2026.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Total véhicules</div><div class="metric-value">{total_vehicles}</div></div>
      <div class="metric-card"><div class="metric-label">Casa Aéroport</div><div class="metric-value">{casa_count}</div></div>
      <div class="metric-card"><div class="metric-label">Fès Aéroport</div><div class="metric-value">{fes_count}</div></div>
      <div class="metric-card"><div class="metric-label">Total HT</div><div class="metric-value">{_dh_major(total_ht)}</div></div>
      <div class="metric-card"><div class="metric-label">TVA 20%</div><div class="metric-value">{_dh_major(tva)}</div></div>
      <div class="metric-card"><div class="metric-label">Total TTC</div><div class="metric-value">{_dh_major(ttc)}</div></div>
    </section>
    <section class="card" style="padding:18px; margin-bottom:16px;">
      <h2>Facturation</h2>
      <p>Numéro de suivi facture : <strong>{invoice_number}</strong></p>
      <p>Convention désormais : <strong>EW-B2B-[CLIENT]-[ANNÉE]-0001</strong>, puis incrémentation 0002, 0003… par client B2B.</p>
    </section>
    <section class="card" style="padding:18px; margin-bottom:16px;">
      <h2>Fichiers Hertz</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:1.2fr .6fr .5fr .45fr;"><span>Fichier</span><span>Modifié</span><span>Taille</span><span>Action</span></div>
        {file_rows}
      </div>
    </section>
    <section class="card" style="padding:18px;">
      <h2>Consultation des véhicules</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:.55fr .75fr 1.25fr .8fr .45fr .7fr;"><span>Date</span><span>Site</span><span>Voiture</span><span>Matricule</span><span>HT</span><span>Notes</span></div>
        {table_rows}
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/b2b"))


def _tracking_page(*, locale: str) -> HTMLResponse:
    title = t("nav.tracking", locale)
    engine = _configured_engine()
    if engine is None:
        body = f"""
        <div class="hero"><div><div class="eyebrow">Operational tracking</div><h1>{escape(title)}</h1></div></div>
        <section class="empty-panel">
          <h2>Not configured</h2>
          <p>Set DATABASE_URL to enable persistent operational service tracking.</p>
        </section>
        """
        return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/tracking"))

    today = date.today()
    with session_scope(engine) as session:
        summary = operational_summary_for_day(session, business_date=today)
        recent_records = recent_operational_service_records(session, limit=100)
        today_records = session.scalars(
            select(OperationalServiceRecordRow)
            .where(OperationalServiceRecordRow.service_date == today, OperationalServiceRecordRow.status != "voided")
            .order_by(
                OperationalServiceRecordRow.client_name.asc(),
                OperationalServiceRecordRow.site_name.asc(),
                OperationalServiceRecordRow.source_order.asc(),
                OperationalServiceRecordRow.id.asc(),
            )
        ).all()

    site_totals: dict[tuple[str, str], dict[str, int]] = {}
    for row in today_records:
        key = (row.client_name, row.site_name)
        bucket = site_totals.setdefault(key, {"count": 0, "total": 0, "review": 0})
        bucket["count"] += 1
        bucket["total"] += int(row.unit_price_ht or 0)
        if row.status in TRACKING_REVIEW_STATUSES:
            bucket["review"] += 1
    site_cards = "".join(
        "<div class='metric-card'>"
        f"<div class='metric-label'>{escape(client)} {escape(site)}</div>"
        f"<div class='metric-value'>{values['count']}</div>"
        f"<div class='metric-note'>{escape(_dh_major(values['total']))} HT · {values['review']} à vérifier</div>"
        "</div>"
        for (client, site), values in sorted(site_totals.items())
    ) or "<div class='metric-card'><div class='metric-label'>Aucun site aujourd’hui</div><div class='metric-value'>0</div></div>"

    rows = "".join(
        "<div class='table-row' style='grid-template-columns:.7fr 1.25fr 1.2fr 1fr .9fr .7fr .9fr;'>"
        f"<span>{escape(row.service_date.isoformat() if row.service_date else '')}</span>"
        f"<span>{escape(row.client_name)}<br><small>{escape(row.site_name)}</small></span>"
        f"<span>{escape(row.vehicle_model or '—')}</span>"
        f"<span>{escape(row.matricule or '—')}</span>"
        f"<span>{escape(row.prestation or '—')}</span>"
        f"<span>{escape(_dh_major(row.unit_price_ht))}</span>"
        f"<span>{escape(row.status)}</span>"
        "</div>"
        for row in recent_records
    ) or "<div class='table-row'><span>No operational records yet.</span><span></span><span></span></div>"

    body = f"""
    <div class="hero"><div><div class="eyebrow">WhatsApp operations</div><h1>{escape(title)}</h1><p>Source de vérité pour les prestations suivies depuis WhatsApp : client, site, véhicule, matricule, photo et tarif HT.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Véhicules aujourd’hui</div><div class="metric-value">{summary.record_count}</div></div>
      <div class="metric-card"><div class="metric-label">Total HT aujourd’hui</div><div class="metric-value">{escape(_dh_major(summary.total_ht_dh))}</div></div>
      <div class="metric-card"><div class="metric-label">À vérifier</div><div class="metric-value">{summary.needs_review_count}</div></div>
      <div class="metric-card"><div class="metric-label">Dernières lignes</div><div class="metric-value">{len(recent_records)}</div></div>
    </section>
    <section class="metric-grid">{site_cards}</section>
    <section class="card" style="padding:18px;">
      <h2>Dernières prestations suivies</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:.7fr 1.25fr 1.2fr 1fr .9fr .7fr .9fr;"><span>Date</span><span>Client / site</span><span>Véhicule</span><span>Matricule</span><span>Prestation</span><span>HT</span><span>Statut</span></div>
        {rows}
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/tracking"))


def _cash_category_select(row: CashLedgerEntryRow, *, locale: str) -> str:
    options = "".join(
        f'<option value="{escape(category)}"{selected}>'
        f'{escape(category.replace("_", " "))}</option>'
        for category in _CASH_CATEGORY_OPTIONS
        if category in CASH_LEDGER_CATEGORIES
        for selected in (" selected" if category == row.category else "",)
    )
    return (
        f'<form class="inline-form" method="post" action="/admin/cash/category?lang={escape(locale)}">'
        f'<input type="hidden" name="entry_id" value="{row.id}">'
        f'<select name="category" aria-label="Category for cash entry {row.id}">{options}</select>'
        '<button type="submit">Save</button>'
        '</form>'
    )


def _cash_page(*, locale: str) -> HTMLResponse:
    title = "Cash"
    engine = _configured_engine()
    if engine is None or not settings.cash_ledger_owner_phone:
        body = f"""
        <div class="hero"><div><div class="eyebrow">Cash ledger</div><h1>{escape(title)}</h1></div></div>
        <section class="empty-panel">
          <h2>Not configured</h2>
          <p>Set CASH_LEDGER_OWNER_PHONE to Omar's WhatsApp number and DATABASE_URL to enable the operational cash ledger.</p>
        </section>
        """
        return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/cash"))

    owner_phone = normalize_phone(settings.cash_ledger_owner_phone)
    today = date.today()
    with session_scope(engine) as session:
        summary = cash_summary_for_day(session, owner_phone=owner_phone, business_date=today)
        balance_minor = expected_cash_balance_minor(session, owner_phone=owner_phone)
        entries = session.scalars(
            select(CashLedgerEntryRow)
            .where(CashLedgerEntryRow.owner_phone == owner_phone)
            .order_by(CashLedgerEntryRow.occurred_at.desc(), CashLedgerEntryRow.id.desc())
            .limit(100)
        ).all()

    rows = "".join(
        "<div class='table-row' style='grid-template-columns:.9fr .65fr .8fr 1.65fr 1fr 1fr .8fr;'>"
        f"<span>{escape(row.occurred_at.strftime('%Y-%m-%d %H:%M') if row.occurred_at else '')}</span>"
        f"<span>{escape(row.direction)}</span>"
        f"<span>{escape(_money_major(row.amount_minor))}</span>"
        f"<span>{_cash_category_select(row, locale=locale)}</span>"
        f"<span>{escape(row.counterparty or '—')}</span>"
        f"<span>{escape(row.status)}</span>"
        f"<span>{escape(f'{row.confidence:.0%}')}</span>"
        "</div>"
        for row in entries
    ) or "<div class='table-row'><span>No cash entries yet.</span><span></span><span></span></div>"
    body = f"""
    <div class="hero"><div><div class="eyebrow">Cash ledger assistant</div><h1>{escape(title)}</h1><p>Operational cash tracking for Omar. WhatsApp voice/text remains the primary input; this dashboard shows the ledger and review queue.</p></div></div>
    <section class="metric-grid">
      <div class="metric-card"><div class="metric-label">Cash in today</div><div class="metric-value">{escape(_money_major(summary.cash_in_minor))}</div></div>
      <div class="metric-card"><div class="metric-label">Cash out today</div><div class="metric-value">{escape(_money_major(summary.cash_out_minor))}</div></div>
      <div class="metric-card"><div class="metric-label">Expected cash on hand</div><div class="metric-value">{escape(_money_major(balance_minor))}</div></div>
      <div class="metric-card"><div class="metric-label">Needs review</div><div class="metric-value">{summary.needs_review_count}</div></div>
    </section>
    <section class="card" style="padding:18px;">
      <h2>Recent ledger entries</h2>
      <div class="table-shell">
        <div class="table-row table-head" style="grid-template-columns:.9fr .65fr .8fr 1.65fr 1fr 1fr .8fr;"><span>Date</span><span>Direction</span><span>Amount</span><span>Category</span><span>Counterparty</span><span>Status</span><span>Confidence</span></div>
        {rows}
      </div>
    </section>
    """
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body, active_path="/admin/cash"))


def _money_major(amount_minor: int) -> str:
    whole, cents = divmod(abs(int(amount_minor or 0)), 100)
    sign = "-" if amount_minor < 0 else ""
    if cents:
        return f"{sign}{whole:,}.{cents:02d} MAD".replace(",", " ")
    return f"{sign}{whole:,} MAD".replace(",", " ")


def _placeholder_page(*, locale: str, page_key: str, active_path: str) -> HTMLResponse:
    title = t(page_key, locale)
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(t('admin.page.placeholder', locale))}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="dashboard-grid">
  <article class="empty-panel">
    <h2>{escape(t('admin.page.what_is_ready', locale))}</h2>
    <p>{escape(t('admin.page.ready_body', locale))}</p>
  </article>
  <aside class="empty-panel">
    <h2>{escape(t('admin.panel.next_steps', locale))}</h2>
    <p>{escape(t('admin.page.next_body', locale))}</p>
    <p><a href="/admin">{escape(t('nav.dashboard', locale))}</a></p>
  </aside>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path=active_path),
        status_code=200,
    )

def _booking_service_cell(item) -> str:
    html = escape(item.service_label)
    if item.addon_service_label:
        html += f"<br><small>Esthétique : {escape(item.addon_service_label)}</small>"
    return html


def _booking_action_cell(item, *, locale: str) -> str:
    if item.status != "pending_ewash_confirmation" or not item.ref:
        return "—"
    return (
        f'<form class="inline-form" method="post" action="/admin/bookings/confirm?lang={escape(locale)}">'
        f'<input type="hidden" name="ref" value="{escape(item.ref)}">'
        f'<button type="submit">{escape(t("admin.bookings.confirm_action", locale))}</button>'
        "</form>"
    )


def _bookings_page(*, locale: str, message: str = "", error: str = "", status_code: int = 200) -> HTMLResponse:
    title = t("nav.bookings", locale)
    bookings = admin_booking_list()
    notice = _notice_html(message=message, error=error)
    if bookings:
        rows = "".join(
            "<div class=\"table-row booking-row\">"
            f'<span>{escape(item.ref)}<br>{_source_badge(item.source, locale=locale)}</span>'
            f"<span>{escape(item.customer_name)}<br><small>{_phone_display(item.customer_phone)}</small></span>"
            f"<span>{escape(item.vehicle_label)}</span>"
            f"<span>{_booking_service_cell(item)}</span>"
            f"<span>{escape(item.date_label)}<br><small>{escape(item.slot)}</small></span>"
            f"<span>{escape(_status_label(item.status, locale))}</span>"
            f"<span>{item.price_dh} DH</span>"
            f"<span>{_booking_action_cell(item, locale=locale)}</span>"
            "</div>"
            for item in bookings
        )
    else:
        rows = '<div class="table-row booking-row"><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span></div>'
    if not settings.database_url:
        intro = "Mode temporaire : Railway Postgres / DATABASE_URL n'est pas configuré. Les réservations affichées ici viennent de la mémoire live et disparaissent au redéploiement."
    elif bookings:
        intro = f"{len(bookings)} réservation(s) confirmée(s) persistée(s)."
    else:
        intro = t("admin.panel.no_bookings", locale)

    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="empty-panel">
  <h2>{escape(t('admin.panel.recent_bookings', locale))}</h2>
  {notice}
  <div class="table-shell" aria-label="Réservations persistées">
    <div class="table-row table-head booking-row"><span>Réf</span><span>Client</span><span>Véhicule</span><span>Service</span><span>Date</span><span>Statut</span><span>Prix</span><span>Action</span></div>
    {rows}
  </div>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/bookings"),
        status_code=status_code,
    )


def _customer_erase_form(*, phone: str, locale: str) -> str:
    return f"""
<form class="inline-form erase-form" method="post" action="/admin/customers/{escape(phone)}/erase?lang={escape(locale)}">
  <strong>{escape(t('admin.customers.danger_zone', locale))}</strong>
  <small class="muted">{escape(t('admin.customers.erase_body', locale))}</small>
  <input name="confirm" placeholder="{escape(t('admin.customers.erase_confirm_prompt', locale))}" autocomplete="off" required>
  <textarea name="notes" placeholder="{escape(t('admin.customers.erase_notes', locale))}"></textarea>
  <button class="danger" type="submit">{escape(t('admin.customers.erase_button', locale))}</button>
</form>"""


def _customers_page(*, locale: str, message: str = "", error: str = "") -> HTMLResponse:
    title = t("nav.customers", locale)
    customers = admin_customer_list()
    if customers:
        rows = "".join(
            "<div class=\"table-row customer-row\">"
            f"<span>{escape(item.display_name)}</span>"
            f"<span>{_phone_display(item.phone)}</span>"
            f"<span>{escape(', '.join(item.vehicle_labels) or '—')}</span>"
            f"<span>{item.booking_count} réservation{'s' if item.booking_count != 1 else ''}</span>"
            f"<span>{escape(item.last_bot_stage_label or '—')}<br><small>{escape(item.last_bot_stage or '')}</small></span>"
            f"<span>{_customer_erase_form(phone=item.phone, locale=locale)}</span>"
            "</div>"
            for item in customers
        )
    else:
        rows = '<div class="table-row customer-row"><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span></div>'
    if not settings.database_url:
        intro = "Mode temporaire : Railway Postgres / DATABASE_URL n'est pas configuré. Les clients affichés ici viennent de la mémoire live et disparaissent au redéploiement."
    elif customers:
        intro = f"{len(customers)} client(s) persisté(s) en base."
    else:
        intro = "Aucun client persisté pour le moment. Les clients apparaissent ici dès qu'ils entrent dans le parcours WhatsApp."
    notice = _notice_html(message=message, error=error)

    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="empty-panel">
  <h2>{escape(title)}</h2>
  {notice}
  <div class="table-shell" aria-label="Clients persistés">
    <div class="table-row table-head customer-row"><span>Client</span><span>Téléphone</span><span>Véhicules</span><span>Réservations</span><span>Étape WhatsApp</span><span>{escape(t('admin.customers.danger_zone', locale))}</span></div>
    {rows}
  </div>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/customers"),
        status_code=200,
    )


def _erasures_page(*, locale: str, actor: str = "") -> HTMLResponse:
    title = t("admin.erasures.title", locale)
    rows_data = recent_erasures(limit=100, actor=actor or None)
    if rows_data:
        rows = "".join(
            "<div class=\"table-row erasure-row\">"
            f"<span>{escape(row['phone_hash'])}</span>"
            f"<span>{escape(row['actor'])}</span>"
            f"<span>{row['deleted_count']}</span>"
            f"<span>{row['anonymized_bookings']}</span>"
            f"<span>{escape(row['performed_at'])}</span>"
            f"<span>{escape(row['notes'] or '—')}</span>"
            "</div>"
            for row in rows_data
        )
    else:
        rows = '<div class="table-row erasure-row"><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span></div>'
    filter_input = escape(actor)
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(t('admin.erasures.intro', locale))}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> {_ADMIN_VERSION}</div>

</section>
<section class="empty-panel">
  <h2>{escape(title)}</h2>
  <form class="inline-form" method="get" action="/admin/erasures">
    <input type="hidden" name="lang" value="{escape(locale)}">
    <label for="actor">{escape(t('admin.erasures.actor', locale))}</label>
    <input id="actor" name="actor" value="{filter_input}" placeholder="customer_self_serve">
    <button type="submit">{escape(t('action.save', locale))}</button>
  </form>
  <div class="table-shell" aria-label="{escape(title)}">
    <div class="table-row table-head erasure-row"><span>{escape(t('admin.erasures.phone_hash', locale))}</span><span>{escape(t('admin.erasures.actor', locale))}</span><span>{escape(t('admin.erasures.deleted_count', locale))}</span><span>{escape(t('admin.erasures.anonymized_bookings', locale))}</span><span>{escape(t('admin.erasures.performed_at', locale))}</span><span>{escape(t('admin.erasures.notes', locale))}</span></div>
    {rows}
  </div>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/erasures"),
        status_code=200,
    )


@router.get("", response_class=HTMLResponse)
async def admin_index(request: Request, lang: str | None = Query(default=None)) -> HTMLResponse:
    locale = normalize_locale(lang or settings.admin_default_locale)

    if not settings.admin_password:
        title = t("admin.not_configured.title", locale)
        body = (
            f"<h1>{escape(title)}</h1>"
            f"<p>{escape(t('admin.not_configured.body', locale))}</p>"
        )
        return HTMLResponse(
            content=_layout(locale=locale, title=title, body=body),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    return _dashboard(locale=locale)


@router.post("", response_class=HTMLResponse)
async def admin_password_submit(request: Request, lang: str | None = Query(default=None)) -> HTMLResponse:
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)

    raw_body = (await request.body()).decode("utf-8")
    supplied_password = parse_qs(raw_body).get("password", [""])[0]
    if not secrets.compare_digest(supplied_password, settings.admin_password):
        return HTMLResponse(
            content=_password_form(locale=locale, error=t("admin.password.invalid", locale)).body.decode("utf-8"),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=_make_session_token(),
        max_age=settings.admin_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.admin_cookie_secure,
    )
    return response


@router.get("/logout")
async def admin_logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(_SESSION_COOKIE)
    return response


@router.post("/prices", response_class=HTMLResponse)
async def admin_prices_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    raw_body = (await request.body()).decode("utf-8")
    form = parse_qs(raw_body)
    valid_pairs = {
        (service_id, category)
        for service_id, _name, _desc, _prices in SERVICES_WASH + SERVICES_DETAILING
        for category in catalog.CAR_PRICE_CATEGORIES
    }
    valid_pairs.update((service_id, catalog.MOTO_PRICE_CATEGORY) for service_id, _name, _desc, _price in SERVICES_MOTO)
    updates: dict[tuple[str, str], int] = {}
    try:
        for key, values in form.items():
            if not key.startswith("price__"):
                continue
            _prefix, service_id, category = key.split("__", 2)
            if (service_id, category) not in valid_pairs:
                raise ValueError(f"Champ prix inconnu: {service_id}/{category}")
            updates[(service_id, category)] = _parse_int_field(values[0], label=f"{service_id} {category}")
        if not updates:
            raise ValueError("Aucun prix à enregistrer")
        catalog.upsert_public_prices(updates)
    except Exception as exc:
        return _prices_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/prices?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/promos", response_class=HTMLResponse)
async def admin_promos_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    raw_body = (await request.body()).decode("utf-8")
    form = parse_qs(raw_body)
    valid_pairs = {
        (service_id, category)
        for service_id, _name, _desc, _prices in SERVICES_WASH + SERVICES_DETAILING
        for category in catalog.CAR_PRICE_CATEGORIES
    }
    discounts: dict[tuple[str, str], int] = {}
    try:
        for key, values in form.items():
            if not key.startswith("discount__"):
                continue
            value = (values[0] if values else "").strip()
            if not value:
                continue
            _prefix, service_id, category = key.split("__", 2)
            if (service_id, category) not in valid_pairs:
                raise ValueError(f"Champ promo inconnu: {service_id}/{category}")
            parsed_price = _parse_int_field(value, label=f"{service_id} {category}")
            public_price = catalog.public_service_price(service_id, category)
            if public_price is not None and parsed_price == public_price:
                continue
            discounts[(service_id, category)] = parsed_price
        catalog.upsert_promo_code(
            code=(form.get("code", [""])[0]),
            label=(form.get("label", [""])[0]),
            active="active" in form,
            discounts=discounts,
        )
    except Exception as exc:
        return _promos_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/promos?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/bookings/confirm", response_class=HTMLResponse)
async def admin_booking_confirm_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        confirm_booking_by_ewash(form.get("ref", [""])[0])
    except BookingLockBusy:
        return _bookings_page(
            locale=locale,
            error="Une autre confirmation admin est déjà en cours pour cette réservation — actualisez puis réessayez.",
            status_code=status.HTTP_409_CONFLICT,
        )
    except Exception as exc:
        return _bookings_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/bookings?lang={locale}&confirmed=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/customers/{phone}/erase", response_class=HTMLResponse)
async def admin_customer_erase_submit(request: Request, phone: str, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    confirm = (form.get("confirm", [""])[0] or "").strip()
    notes = (form.get("notes", [""])[0] or "")[:500]
    if confirm != "ERASE":
        return RedirectResponse(
            url=f"/admin/customers?lang={locale}&error=confirm_required",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Audit actor needs to distinguish admin sessions for compliance. The
    # session cookie's timestamp prefix is unique per login (admins sharing
    # one password still get a fresh ts on each /admin POST), and the client
    # IP narrows it further. Truncated to the 64-char audit column cap.
    session_token = request.cookies.get(_SESSION_COOKIE) or ""
    session_ts = session_token.split(":", 1)[0] if ":" in session_token else "anon"
    client_host = (request.client.host if request.client else "") or "unknown"
    actor = f"admin:{session_ts}:{client_host}"[:64]
    result = anonymize_customer(phone, actor=actor, notes=notes)
    phone_hash = sha256(phone.encode("utf-8")).hexdigest()[:12]
    log.info(
        "admin.erase phone_hash=%s actor=%s deleted_count=%d anonymized_bookings=%d",
        phone_hash,
        actor,
        result["deleted_count"],
        result["anonymized_bookings"],
    )
    return RedirectResponse(
        url=f"/admin/customers?lang={locale}&erased={result['anonymized_bookings']}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _admin_form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


def _auth_or_none(request: Request, locale: str):
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)
    return None


@router.post("/cash/category", response_class=HTMLResponse)
async def admin_cash_category_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    engine = _configured_engine()
    if engine is None or not settings.cash_ledger_owner_phone:
        return RedirectResponse(url=f"/admin/cash?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)

    form = await _admin_form(request)
    owner_phone = normalize_phone(settings.cash_ledger_owner_phone)
    session_token = request.cookies.get(_SESSION_COOKIE) or ""
    session_ts = session_token.split(":", 1)[0] if ":" in session_token else "anon"
    client_host = (request.client.host if request.client else "") or "unknown"
    actor = f"admin:{session_ts}:{client_host}"[:64]
    try:
        entry_id = int(form.get("entry_id", ["0"])[0] or 0)
        category = (form.get("category", [""])[0] or "").strip()
        with session_scope(engine) as session:
            entry = session.get(CashLedgerEntryRow, entry_id)
            if entry is None or entry.owner_phone != owner_phone:
                raise ValueError("cash ledger entry not found")
            review_cash_ledger_category(
                session,
                entry_id=entry_id,
                actor_phone=actor,
                category=category,
            )
    except Exception as exc:
        log.warning("admin.cash.category_update_failed error=%s", exc)
        return RedirectResponse(url=f"/admin/cash?lang={locale}&error=category", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/admin/cash?lang={locale}&updated=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reminders", response_class=HTMLResponse)
async def admin_reminders_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        catalog.upsert_reminder_rule(
            name=form.get("reminder_name", [""])[0],
            offset_minutes_before=int(form.get("offset_minutes_before", ["0"])[0] or 0),
            template_name=form.get("template_name", [""])[0],
            enabled="enabled" in form,
        )
    except Exception as exc:
        return _reminders_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/reminders?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/notifications", response_class=HTMLResponse)
async def admin_notifications_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        upsert_booking_notification_settings(
            enabled="enabled" in form,
            phone_number=form.get("phone_number", [""])[0],
            template_name=form.get("template_name", [""])[0],
            template_language=form.get("template_language", ["fr"])[0] or "fr",
        )
    except Exception as exc:
        return _notifications_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/notifications?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/closed-dates", response_class=HTMLResponse)
async def admin_closed_dates_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        catalog.upsert_closed_date(
            date_iso=form.get("closed_date", [""])[0],
            label=form.get("label", [""])[0],
            active="active" in form,
        )
    except Exception as exc:
        return _closed_dates_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/closed-dates?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/time-slots", response_class=HTMLResponse)
async def admin_time_slots_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        catalog.upsert_time_slot(
            slot_id=form.get("slot_id", [""])[0],
            label=form.get("label", [""])[0],
            period=form.get("period", [""])[0],
            active="active" in form,
        )
    except Exception as exc:
        return _time_slots_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/time-slots?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/centers", response_class=HTMLResponse)
async def admin_centers_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        catalog.upsert_center(
            center_id=form.get("center_id", [""])[0],
            name=form.get("name", [""])[0],
            details=form.get("details", [""])[0],
            active="active" in form,
        )
    except Exception as exc:
        return _centers_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/centers?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/copy", response_class=HTMLResponse)
async def admin_copy_submit(request: Request, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if response := _auth_or_none(request, locale):
        return response
    form = await _admin_form(request)
    try:
        catalog.upsert_text_snippet(
            key=form.get("text_key", [""])[0],
            title=form.get("title", [""])[0],
            body=form.get("body", [""])[0],
        )
    except Exception as exc:
        return _copy_page(locale=locale, error=str(exc))
    return RedirectResponse(url=f"/admin/copy?lang={locale}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/payroll/receipts/event/{event_id}")
async def admin_payroll_receipt_event(request: Request, event_id: int, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    engine = _configured_engine()
    if engine is None:
        return HTMLResponse(content="Not configured", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    with session_scope(engine) as session:
        row = session.get(AgentPayrollEventRow, event_id)
        if row is None:
            return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
        payload = _agent_payroll_raw_payload(row)
        encoded_image = payload.get("receipt_image_base64")
        mime_type = str(payload.get("receipt_mime_type") or "image/jpeg")
        filename = _payroll_receipt_filename(row) or f"payroll-receipt-{event_id}.jpg"
    if not isinstance(encoded_image, str) or not encoded_image:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    try:
        content = base64.b64decode(encoded_image, validate=True)
    except Exception:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/payroll/receipts/{filename}")
async def admin_payroll_receipt(request: Request, filename: str, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    safe_name = filename.replace("\\", "/")
    if "/" in safe_name or safe_name in {"", ".", ".."}:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    path = (_PAYROLL_RECEIPT_DIR / safe_name).resolve()
    receipt_root = _PAYROLL_RECEIPT_DIR.resolve()
    if path.parent != receipt_root or path.suffix.lower() not in _PAYROLL_RECEIPT_EXTENSIONS:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    if not path.is_file():
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path)


@router.get("/b2b/hertz", response_class=HTMLResponse)
async def admin_b2b_hertz(request: Request, lang: str | None = Query(default=None)) -> HTMLResponse:
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        title = t("admin.not_configured.title", locale)
        body = f"<h1>{escape(title)}</h1><p>{escape(t('admin.not_configured.body', locale))}</p>"
        return HTMLResponse(
            content=_layout(locale=locale, title=title, body=body, active_path="/admin/b2b"),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)
    return _b2b_hertz_page(locale=locale)


@router.get("/b2b/hertz/files/{filename}")
async def admin_b2b_hertz_file(request: Request, filename: str, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    safe_name = _safe_b2b_hertz_filename(filename)
    if not safe_name:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)

    engine = _configured_engine()
    if engine is not None:
        with session_scope(engine) as session:
            row = None
            direct_key = f"{_B2B_HERTZ_FILE_TEXT_PREFIX}{safe_name}"
            if len(direct_key) <= 80:
                row = session.get(AdminTextRow, direct_key)
            if row is None:
                candidates = session.scalars(
                    select(AdminTextRow).where(AdminTextRow.text_key.like(f"{_B2B_HERTZ_FILE_TEXT_PREFIX}%"))
                ).all()
                for candidate in candidates:
                    candidate_payload = _json_payload(candidate)
                    if candidate_payload.get("filename") == safe_name:
                        row = candidate
                        break
            payload = _json_payload(row)
        encoded_file = payload.get("content_base64")
        media_type = str(payload.get("content_type") or "application/octet-stream")
        if isinstance(encoded_file, str) and encoded_file:
            try:
                content = base64.b64decode(encoded_file, validate=True)
            except Exception:
                content = b""
            if content:
                return Response(
                    content=content,
                    media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )

    path = (_B2B_HERTZ_DIR / safe_name).resolve()
    hertz_root = _B2B_HERTZ_DIR.resolve()
    if path.parent != hertz_root or not path.is_file():
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path, filename=path.name)


@router.get("/personal-finances/files/{filename}")
async def admin_personal_finance_file(request: Request, filename: str, lang: str | None = Query(default=None)):
    locale = normalize_locale(lang or settings.admin_default_locale)
    if not settings.admin_password:
        return RedirectResponse(url=f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)

    safe_name = _safe_personal_finance_filename(filename)
    if not safe_name:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)

    engine = _configured_engine()
    if engine is not None:
        with session_scope(engine) as session:
            row = session.get(AdminTextRow, f"{_PERSONAL_FINANCE_TEXT_PREFIX}{safe_name}")
            payload = _personal_finance_payload(row) if row else {}
        encoded_workbook = payload.get("content_base64")
        if isinstance(encoded_workbook, str) and encoded_workbook:
            try:
                content = base64.b64decode(encoded_workbook, validate=True)
            except Exception:
                content = b""
            if content:
                return Response(
                    content=content,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )

    path = (_PERSONAL_FINANCE_DIR / safe_name).resolve()
    finance_root = _PERSONAL_FINANCE_DIR.resolve()
    if path.parent != finance_root or not path.is_file():
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/{page_slug}", response_class=HTMLResponse)
async def admin_section(request: Request, page_slug: str, lang: str | None = Query(default=None)) -> HTMLResponse:
    locale = normalize_locale(lang or settings.admin_default_locale)
    page = _PAGE_BY_SLUG.get(page_slug)
    if page is None:
        return HTMLResponse(content="Not found", status_code=status.HTTP_404_NOT_FOUND)

    page_id, page_key, active_path = page
    if not settings.admin_password:
        title = t("admin.not_configured.title", locale)
        body = (
            f"<h1>{escape(title)}</h1>"
            f"<p>{escape(t('admin.not_configured.body', locale))}</p>"
        )
        return HTMLResponse(
            content=_layout(locale=locale, title=title, body=body, active_path=active_path),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not _valid_session_token(request.cookies.get(_SESSION_COOKIE)):
        return _password_form(locale=locale)
    if page_id == "bookings":
        message = t("admin.bookings.confirmed", locale) if request.query_params.get("confirmed") == "1" else ""
        return _bookings_page(locale=locale, message=message)
    if page_id == "customers":
        error = ""
        message = ""
        if request.query_params.get("error") == "confirm_required":
            error = t("admin.customers.erase_confirm_required", locale)
        erased = request.query_params.get("erased")
        if erased is not None:
            message = t("admin.customers.erased", locale).format(count=erased)
        return _customers_page(locale=locale, message=message, error=error)
    if page_id == "b2b":
        return _b2b_page(locale=locale)
    if page_id == "payroll":
        return _payroll_page(locale=locale)
    if page_id == "tracking":
        return _tracking_page(locale=locale)
    if page_id == "cash":
        return _cash_page(locale=locale)
    if page_id == "personal_finances":
        return _personal_finances_page(locale=locale)
    if page_id == "erasures":
        actor = (request.query_params.get("actor") or "").strip()
        return _erasures_page(locale=locale, actor=actor)
    if page_id == "prices":
        message = t("admin.prices.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _prices_page(locale=locale, message=message)
    if page_id == "promos":
        message = t("admin.promos.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _promos_page(locale=locale, message=message)
    if page_id == "reminders":
        message = t("admin.reminders.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _reminders_page(locale=locale, message=message)
    if page_id == "notifications":
        message = t("admin.notifications.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _notifications_page(locale=locale, message=message)
    if page_id == "closed_dates":
        message = t("admin.closed_dates.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _closed_dates_page(locale=locale, message=message)
    if page_id == "time_slots":
        message = t("admin.time_slots.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _time_slots_page(locale=locale, message=message)
    if page_id == "centers":
        message = t("admin.centers.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _centers_page(locale=locale, message=message)
    if page_id == "copy":
        message = t("admin.copy.saved", locale) if request.query_params.get("saved") == "1" else ""
        return _copy_page(locale=locale, message=message)
    return _placeholder_page(locale=locale, page_key=page_key, active_path=active_path)
