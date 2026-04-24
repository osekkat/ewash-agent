"""French-first admin portal routes.

This is the v0.3 shell. It is intentionally inert until admin credentials are
configured, so deploying the implementation slice does not expose booking ops.
"""
from __future__ import annotations

import hmac
import secrets
import time
from hashlib import sha256
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from .admin_i18n import SUPPORTED_LOCALES, normalize_locale, t
from .config import settings
from .persistence import admin_booking_list, admin_customer_list, admin_dashboard_summary

router = APIRouter(prefix="/admin", tags=["admin"])
_SESSION_COOKIE = "ewash_admin_session"
_NAV_ITEMS = (
    ("dashboard", "nav.dashboard", "/admin"),
    ("bookings", "nav.bookings", "/admin/bookings"),
    ("customers", "nav.customers", "/admin/customers"),
    ("prices", "nav.prices", "/admin/prices"),
    ("promos", "nav.promos", "/admin/promos"),
    ("reminders", "nav.reminders", "/admin/reminders"),
    ("closed_dates", "nav.closed_dates", "/admin/closed-dates"),
    ("time_slots", "nav.time_slots", "/admin/time-slots"),
    ("centers", "nav.centers", "/admin/centers"),
    ("copy", "nav.copy", "/admin/copy"),
)
_PAGE_BY_SLUG = {path.rsplit("/", 1)[-1]: (page_id, key, path) for page_id, key, path in _NAV_ITEMS if path != "/admin"}


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
    .booking-row {{ grid-template-columns: .8fr 1.1fr 1fr 1.3fr .9fr .9fr .7fr; align-items: center; }}
    .customer-row {{ grid-template-columns: 1.2fr 1fr 1.4fr .8fr; align-items: center; }}
    .table-row:last-child {{ border-bottom: 0; }}
    .table-head {{ color: var(--soft); background: rgba(255,255,255,0.03); font-weight: 510; }}
    .status-list {{ display: grid; gap: 10px; margin-top: 18px; }}
    .status-item {{ display: flex; justify-content: space-between; align-items: center; padding: 12px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-soft); color: var(--soft); }}
    .dot {{ width: 8px; height: 8px; border-radius: 999px; background: var(--good); display: inline-block; margin-right: 8px; }}
    .soon {{ color: var(--muted); font-size: 13px; }}
    form {{ max-width: 420px; margin-top: 24px; padding: 22px; border: 1px solid var(--border); border-radius: 16px; background: rgba(255,255,255,0.035); }}
    label {{ color: var(--soft); font-size: 14px; }}
    input {{ width: 100%; margin: 8px 0 14px; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--border); color: var(--text); background: rgba(255,255,255,0.04); }}
    button {{ border: 0; border-radius: 10px; padding: 11px 16px; color: #fff; background: var(--accent-bg); font-weight: 590; cursor: pointer; }}
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
            f"<span>{escape(item.customer_name)}</span>"
            f"<span>{escape(item.service_label)}</span>"
            f"<span>{escape(item.status)}</span>"
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
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> v0.3.0-alpha9</div>
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
    <div class="metric-label">{escape(t('admin.metric.customers', locale))}</div>
    <div class="metric-value">{summary.customers}</div>
    <div class="metric-note">{escape(t('admin.metric.from_db', locale))}</div>
  </article>
  <article class="metric-card">
    <div class="metric-label">{escape(t('admin.metric.reminders', locale))}</div>
    <div class="metric-value">{summary.pending_reminders}</div>
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
      <div class="status-item"><span>{escape(t('admin.next.pages', locale))}</span><span class="soon">{escape(t('admin.next.soon', locale))}</span></div>
    </div>
    <p><a href="/admin/logout">{escape(t('nav.logout', locale))}</a></p>
  </aside>
</section>
"""
    return HTMLResponse(content=_layout(locale=locale, title=title, body=body), status_code=200)


def _placeholder_page(*, locale: str, page_key: str, active_path: str) -> HTMLResponse:
    title = t(page_key, locale)
    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(t('admin.page.placeholder', locale))}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> v0.3.0-alpha9</div>
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

def _bookings_page(*, locale: str) -> HTMLResponse:
    title = t("nav.bookings", locale)
    bookings = admin_booking_list()
    if bookings:
        rows = "".join(
            "<div class=\"table-row booking-row\">"
            f"<span>{escape(item.ref)}</span>"
            f"<span>{escape(item.customer_name)}<br><small>{escape(item.customer_phone)}</small></span>"
            f"<span>{escape(item.vehicle_label)}</span>"
            f"<span>{escape(item.service_label)}</span>"
            f"<span>{escape(item.date_label)}<br><small>{escape(item.slot)}</small></span>"
            f"<span>{escape(item.status)}</span>"
            f"<span>{item.price_dh} DH</span>"
            "</div>"
            for item in bookings
        )
        intro = f"{len(bookings)} réservation(s) confirmée(s) persistée(s)."
    else:
        rows = '<div class="table-row booking-row"><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span><span>—</span></div>'
        intro = t("admin.panel.no_bookings", locale)

    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> v0.3.0-alpha9</div>
</section>
<section class="empty-panel">
  <h2>{escape(t('admin.panel.recent_bookings', locale))}</h2>
  <div class="table-shell" aria-label="Réservations persistées">
    <div class="table-row table-head booking-row"><span>Réf</span><span>Client</span><span>Véhicule</span><span>Service</span><span>Date</span><span>Statut</span><span>Prix</span></div>
    {rows}
  </div>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/bookings"),
        status_code=200,
    )


def _customers_page(*, locale: str) -> HTMLResponse:
    title = t("nav.customers", locale)
    customers = admin_customer_list()
    if customers:
        rows = "".join(
            "<div class=\"table-row customer-row\">"
            f"<span>{escape(item.display_name)}</span>"
            f"<span>{escape(item.phone)}</span>"
            f"<span>{escape(', '.join(item.vehicle_labels) or '—')}</span>"
            f"<span>{item.booking_count} réservation{'s' if item.booking_count != 1 else ''}</span>"
            "</div>"
            for item in customers
        )
        intro = f"{len(customers)} client(s) persisté(s) en base."
    else:
        rows = '<div class="table-row customer-row"><span>—</span><span>—</span><span>—</span><span>—</span></div>'
        intro = "Aucun client persisté pour le moment. Les clients apparaissent ici après une réservation WhatsApp confirmée."

    body = f"""
<section class="hero">
  <div>
    <div class="eyebrow">Ewash Ops</div>
    <h1>{escape(title)}</h1>
    <p>{escape(intro)}</p>
  </div>
  <div class="version-pill"><strong>{escape(t('admin.dashboard.version_label', locale))}</strong> v0.3.0-alpha9</div>
</section>
<section class="empty-panel">
  <h2>{escape(title)}</h2>
  <div class="table-shell" aria-label="Clients persistés">
    <div class="table-row table-head customer-row"><span>Client</span><span>Téléphone</span><span>Véhicules</span><span>Réservations</span></div>
    {rows}
  </div>
</section>
"""
    return HTMLResponse(
        content=_layout(locale=locale, title=title, body=body, active_path="/admin/customers"),
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
    )
    return response


@router.get("/logout")
async def admin_logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(_SESSION_COOKIE)
    return response


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
        return _bookings_page(locale=locale)
    if page_id == "customers":
        return _customers_page(locale=locale)
    return _placeholder_page(locale=locale, page_key=page_key, active_path=active_path)
