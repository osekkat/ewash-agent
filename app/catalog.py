"""Static catalog for Ewash — matches the Apr 2026 printed tariff sheet.

Pricing categories A/B/C are the industry-standard size tiers Ewash prints
on their own flyer. Moto is a separate lane with its own 2-option service list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
import re
import threading

from sqlalchemy import Engine, delete, func, select

from .config import settings
from .db import init_db, make_engine, session_scope
from .models import (
    AdminTextRow,
    BookingNotificationSettingRow,
    CenterRow,
    ClosedDateRow,
    PromoCodeRow,
    PromoDiscountRow,
    ReminderRuleRow,
    ServicePriceRow,
    ServiceRow,
    TimeSlotRow,
)

log = logging.getLogger(__name__)

# ── Vehicle categories ─────────────────────────────────────────────────────
# Shown as a WhatsApp LIST (4 rows). Row format: (id, title ≤24 chars, desc).
# The first char of `title` ("A", "B", "C") is used as the category key.
VEHICLE_CATEGORIES = [
    ("veh_a",    "A — Citadine",          "Clio, Sandero, i10, Picanto…"),
    ("veh_b",    "B — Berline / SUV",     "Megane, Duster, Tucson, Kadjar…"),
    ("veh_c",    "C — Grande berline/SUV","X5, Tiguan, Touareg, Q7…"),
    ("veh_moto", "🏍️ Moto / Scooter",     "Deux roues — tarif unique"),
]

# Map vehicle row id → single-letter pricing category. Moto is handled separately.
VEHICLE_CATEGORY_KEY = {
    "veh_a":    "A",
    "veh_b":    "B",
    "veh_c":    "C",
    "veh_moto": "MOTO",
}

# Clean category labels for API responses (no leading letter / no emoji).
# Distinct from the WhatsApp list-row titles in VEHICLE_CATEGORIES which embed
# the category letter ("A — …") for in-chat clarity.
VEHICLE_CATEGORY_LABEL = {
    "A":    "Citadine",
    "B":    "Berline / SUV",
    "C":    "Grande berline/SUV",
    "MOTO": "Moto/Scooter",
}


# ── Services for cars (A/B/C) ──────────────────────────────────────────────
# Split into 2 buckets, matching the Ewash flyer layout:
#   (1) LAVAGES — core wash formulas (maintenance / weekly recurring)
#   (2) ESTHÉTIQUE — premium detailing (polish, ceramic, renovation, lustre)
# Row format: (id, short_name, description ≤72 chars, prices_dict{A,B,C}).

SERVICES_WASH = [
    ("svc_ext",  "L'Extérieur",  "Carrosserie, vitres, jantes + wax 1 semaine",
        {"A": 60,  "B": 65,   "C": 70}),
    ("svc_cpl",  "Le Complet",   "L'Extérieur + intérieur + aspirateur tapis/sièges",
        {"A": 115, "B": 125,  "C": 135}),
    ("svc_sal",  "Le Salon",     "Le Complet + injection/extraction sièges & tissus",
        {"A": 490, "B": 540,  "C": 590}),
]

SERVICES_DETAILING = [
    ("svc_pol",      "Le Polissage",        "Rénov. carrosserie + protection hydrophobe 4 sem.",
        {"A": 990, "B": 1070, "C": 1150}),
    ("svc_cer6m",    "Céramique 6 mois",        "Protection céramique 6 mois (polissage nécessaire)",
        {"A": 800, "B": 800,  "C": 800}),
    ("svc_cer6w",    "Céramique 6 Semaines",        "Protection céramique express (6 semaines)",
        {"A": 200, "B": 200,  "C": 200}),
    ("svc_cuir",     "Rénov. Cuir",         "Nettoyage & nourrissage des sièges et garnitures cuir",
        {"A": 250, "B": 250,  "C": 250}),
    ("svc_plastq",   "Rénov. Plast.",       "Rénovation & protection plastiques (6 mois)",
        {"A": 150, "B": 150,  "C": 250}),
    ("svc_optq",     "Rénov. Optiques",     "Ponçage + polissage des optiques de phares",
        {"A": 150, "B": 150,  "C": 150}),
    ("svc_lustre",   "Lustrage",            "Lustrage carrosserie (sans polissage)",
        {"A": 600, "B": 600,  "C": 700}),
]

# Backward-compat: flat list used by price/name lookups that scan everything.
SERVICES_CAR = SERVICES_WASH + SERVICES_DETAILING

# ── Services for moto/scooter ──────────────────────────────────────────────
# Single flat price, no category. Row: (id, label, description, price).
SERVICES_MOTO = [
    ("svc_scooter", "Scooter",  "Lavage complet scooter 2 roues", 85),
    ("svc_moto",    "Moto",     "Lavage complet moto",           105),
]


# ── Colors ─────────────────────────────────────────────────────────────────
# Free text only — we accept any color the user types. No buttons.
# (Leaving this list empty so legacy payload-matching paths never fire.)
COLORS: list[tuple[str, str]] = []


# ── Promo codes ────────────────────────────────────────────────────────────
# Per-partner preferential tariffs. When a customer enters a valid code during
# booking, service_price() + build_car_service_rows() use the discounted grid
# instead of the public one. Moto is intentionally excluded — the printed
# flyers show no moto discount on any partner tier.
#
# Add new codes by dropping a new entry keyed on the UPPERCASE code. Keep keys
# alphanumeric (partner DMs + printed flyers tend to be fat-fingered).
PROMO_CODES: dict[str, dict] = {
    "YS26": {
        "label": "Yasmine Signature",
        # Regular → promo price map per service_id × category.
        # Matches "Tarifs Exclusifs Yasmine Signature" Apr-2026 flyer.
        "discounts": {
            "svc_ext":    {"A": 55,  "B": 60,  "C": 65},
            "svc_cpl":    {"A": 100, "B": 110, "C": 120},
            "svc_sal":    {"A": 415, "B": 460, "C": 500},
            "svc_pol":    {"A": 790, "B": 856, "C": 920},
            "svc_cer6m":  {"A": 640, "B": 640, "C": 640},
            "svc_cer6w":  {"A": 160, "B": 160, "C": 160},
            "svc_cuir":   {"A": 200, "B": 200, "C": 200},
            "svc_plastq": {"A": 120, "B": 120, "C": 200},
            "svc_optq":   {"A": 200, "B": 200, "C": 200},
            "svc_lustre": {"A": 480, "B": 480, "C": 560},
            # MOTO intentionally excluded (no partner discount on 2-wheels).
        },
    },
}


CAR_PRICE_CATEGORIES = ("A", "B", "C")
MOTO_PRICE_CATEGORY = "MOTO"


@dataclass(frozen=True)
class PromoCodeView:
    code: str
    label: str
    active: bool
    discounts: dict[tuple[str, str], int]


@dataclass(frozen=True)
class ReminderRuleView:
    id: int | None
    name: str
    enabled: bool
    offset_minutes_before: int
    template_name: str
    channel: str


@dataclass(frozen=True)
class ClosedDateView:
    date_iso: str
    label: str
    active: bool


@dataclass(frozen=True)
class TimeSlotView:
    slot_id: str
    label: str
    period: str
    active: bool
    sort_order: int = 0


@dataclass(frozen=True)
class CenterView:
    center_id: str
    name: str
    details: str
    active: bool
    sort_order: int = 0


@dataclass(frozen=True)
class TextSnippetView:
    key: str
    title: str
    body: str


DEFAULT_TEXT_SNIPPETS: dict[str, tuple[str, str]] = {
    "booking.welcome": ("Accueil réservation", "Bonjour 👋 Bienvenue chez Ewash. On démarre votre réservation."),
    "booking.location": ("Choix du lieu", "Où souhaitez-vous le lavage ? À domicile ou au stand."),
    "booking.promo": ("Question code promo", "Avez-vous un code promo partenaire ?"),
    "booking.note": ("Question note", "Souhaitez-vous ajouter une note ?"),
}


# Lock-protected singleton replaces @lru_cache(maxsize=1) for the same reason
# documented in app/persistence.py: lru_cache is thread-safe for the lookup but
# not the body, so two threads racing the first call would both run init_db's
# create_all + seed + backfills and collide on duplicate-key INSERTs. See
# app/persistence.py for the full rationale.
_engine_lock = threading.Lock()
_engine: Engine | None = None
_engine_initialized = False


def _catalog_engine() -> Engine | None:
    global _engine, _engine_initialized
    if _engine_initialized:
        return _engine
    with _engine_lock:
        if _engine_initialized:
            return _engine
        if not settings.database_url:
            _engine = None
            _engine_initialized = True
            return None
        engine = make_engine(settings.database_url)
        init_db(engine)
        _engine = engine
        _engine_initialized = True
        return _engine


def catalog_cache_clear() -> None:
    """Clear cached DB state after admin catalog writes or test setting changes."""
    global _engine, _engine_initialized
    with _engine_lock:
        _engine = None
        _engine_initialized = False


# Preserve the @lru_cache.cache_clear() surface for any direct callers/tests.
_catalog_engine.cache_clear = catalog_cache_clear  # type: ignore[attr-defined]


def _engine_or_configured(engine: Engine | None = None) -> Engine | None:
    return engine if engine is not None else _catalog_engine()


def _clean_promo_code(text: str) -> str:
    return text.strip().strip("'\"“”‘’ ").upper()


def _default_public_price(service_id: str, category: str) -> int | None:
    if category == MOTO_PRICE_CATEGORY:
        for sid, _name, _desc, price in SERVICES_MOTO:
            if sid == service_id:
                return price
        return None
    for sid, _name, _desc, prices in SERVICES_CAR:
        if sid == service_id:
            return prices.get(category)
    return None


def public_service_price(service_id: str, category: str, *, engine: Engine | None = None) -> int | None:
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                row = session.scalars(
                    select(ServicePriceRow).where(
                        ServicePriceRow.service_id == service_id,
                        ServicePriceRow.category == category,
                    )
                ).first()
                if row is not None:
                    return row.price_dh
        except Exception:
            log.exception("public_service_price failed; falling back to static catalog")
    return _default_public_price(service_id, category)


def upsert_public_prices(updates: dict[tuple[str, str], int], *, engine: Engine | None = None) -> int:
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        raise RuntimeError("DATABASE_URL is not configured")
    count = 0
    with session_scope(db_engine) as session:
        for (service_id, category), price_dh in updates.items():
            row = session.scalars(
                select(ServicePriceRow).where(
                    ServicePriceRow.service_id == service_id,
                    ServicePriceRow.category == category,
                )
            ).first()
            if row is None:
                session.add(ServicePriceRow(service_id=service_id, category=category, price_dh=price_dh))
            else:
                row.price_dh = price_dh
            count += 1
    catalog_cache_clear()
    return count


def _db_promo_view(code: str, *, engine: Engine | None = None) -> PromoCodeView | None:
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return None
    try:
        with session_scope(db_engine) as session:
            row = session.get(PromoCodeRow, code)
            if row is None:
                return None
            discounts = {
                (discount.service_id, discount.category): discount.price_dh
                for discount in session.scalars(
                    select(PromoDiscountRow).where(PromoDiscountRow.promo_code == code)
                ).all()
            }
            return PromoCodeView(code=row.code, label=row.label, active=row.active, discounts=discounts)
    except Exception:
        log.exception("_db_promo_view failed; falling back to static promo catalog")
        return None


def _static_promo_view(code: str) -> PromoCodeView | None:
    promo = PROMO_CODES.get(code)
    if promo is None:
        return None
    discounts: dict[tuple[str, str], int] = {}
    for service_id, prices in promo.get("discounts", {}).items():
        for category, price_dh in prices.items():
            discounts[(service_id, category)] = price_dh
    return PromoCodeView(code=code, label=str(promo.get("label") or code), active=True, discounts=discounts)


def list_promo_codes(*, engine: Engine | None = None) -> tuple[PromoCodeView, ...]:
    promos = {code: view for code in PROMO_CODES if (view := _static_promo_view(code)) is not None}
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                rows = session.scalars(select(PromoCodeRow).order_by(PromoCodeRow.code)).all()
                for row in rows:
                    discounts = {
                        (discount.service_id, discount.category): discount.price_dh
                        for discount in session.scalars(
                            select(PromoDiscountRow).where(PromoDiscountRow.promo_code == row.code)
                        ).all()
                    }
                    promos[row.code] = PromoCodeView(
                        code=row.code,
                        label=row.label,
                        active=row.active,
                        discounts=discounts,
                    )
        except Exception:
            log.exception("list_promo_codes failed; falling back to static promo catalog")
    return tuple(promos[code] for code in sorted(promos))


def upsert_promo_code(
    *,
    code: str,
    label: str,
    active: bool,
    discounts: dict[tuple[str, str], int],
    engine: Engine | None = None,
) -> str:
    normalized = _clean_promo_code(code)
    if not normalized or not re.fullmatch(r"[A-Z0-9_-]{2,40}", normalized):
        raise ValueError("Promo code must be 2-40 letters, numbers, underscores, or hyphens")
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        raise RuntimeError("DATABASE_URL is not configured")
    with session_scope(db_engine) as session:
        row = session.get(PromoCodeRow, normalized)
        if row is None:
            row = PromoCodeRow(code=normalized, label=label.strip() or normalized, active=active)
            session.add(row)
        else:
            row.label = label.strip() or normalized
            row.active = active
        session.execute(delete(PromoDiscountRow).where(PromoDiscountRow.promo_code == normalized))
        for (service_id, category), price_dh in discounts.items():
            session.add(
                PromoDiscountRow(
                    promo_code=normalized,
                    service_id=service_id,
                    category=category,
                    price_dh=price_dh,
                )
            )
    catalog_cache_clear()
    return normalized


def _require_db_engine(engine: Engine | None = None) -> Engine:
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return db_engine


def _clean_id(value: str, *, field: str) -> str:
    cleaned = (value or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{2,80}", cleaned):
        raise ValueError(f"{field} must be 2-80 letters, numbers, underscores, or hyphens")
    return cleaned


def _validate_date_iso(value: str) -> str:
    cleaned = (value or "").strip()
    date.fromisoformat(cleaned)
    return cleaned


def list_reminder_rules(*, engine: Engine | None = None) -> tuple[ReminderRuleView, ...]:
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return ()
    try:
        with session_scope(db_engine) as session:
            rows = session.scalars(select(ReminderRuleRow).order_by(ReminderRuleRow.offset_minutes_before.desc())).all()
            return tuple(
                ReminderRuleView(
                    id=row.id,
                    name=row.name,
                    enabled=row.enabled,
                    offset_minutes_before=row.offset_minutes_before,
                    template_name=row.template_name,
                    channel=row.channel,
                )
                for row in rows
            )
    except Exception:
        log.exception("list_reminder_rules failed")
        return ()


def upsert_reminder_rule(
    *,
    name: str,
    offset_minutes_before: int,
    template_name: str,
    enabled: bool,
    channel: str = "whatsapp_template",
    engine: Engine | None = None,
) -> int:
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ValueError("Reminder name is required")
    if offset_minutes_before <= 0:
        raise ValueError("Reminder offset must be positive")
    db_engine = _require_db_engine(engine)
    with session_scope(db_engine) as session:
        row = session.scalars(select(ReminderRuleRow).where(ReminderRuleRow.name == cleaned_name)).first()
        if row is None:
            row = ReminderRuleRow(name=cleaned_name, offset_minutes_before=offset_minutes_before)
            session.add(row)
        row.enabled = enabled
        row.offset_minutes_before = offset_minutes_before
        row.template_name = (template_name or "").strip()
        row.channel = (channel or "whatsapp_template").strip() or "whatsapp_template"
        session.flush()
        rule_id = int(row.id)
    catalog_cache_clear()
    return rule_id


def list_closed_dates(*, engine: Engine | None = None) -> tuple[ClosedDateView, ...]:
    dates = {value: ClosedDateView(date_iso=value, label="Fermeture", active=True) for value in CLOSED_DATES}
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                for row in session.scalars(select(ClosedDateRow).order_by(ClosedDateRow.date_iso)).all():
                    dates[row.date_iso] = ClosedDateView(row.date_iso, row.label, row.active)
        except Exception:
            log.exception("list_closed_dates failed; falling back to static closures")
    return tuple(dates[key] for key in sorted(dates))


def active_closed_dates(*, engine: Engine | None = None) -> set[str]:
    return {item.date_iso for item in list_closed_dates(engine=engine) if item.active}


def upsert_closed_date(*, date_iso: str, label: str, active: bool, engine: Engine | None = None) -> str:
    normalized = _validate_date_iso(date_iso)
    db_engine = _require_db_engine(engine)
    with session_scope(db_engine) as session:
        row = session.get(ClosedDateRow, normalized)
        if row is None:
            row = ClosedDateRow(date_iso=normalized)
            session.add(row)
        row.label = (label or "").strip() or "Fermeture"
        row.active = active
    catalog_cache_clear()
    return normalized


def list_time_slots(*, engine: Engine | None = None) -> tuple[TimeSlotView, ...]:
    slots = {
        slot_id: TimeSlotView(slot_id, label, period, True, index)
        for index, (slot_id, label, period) in enumerate(SLOTS)
    }
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                for row in session.scalars(select(TimeSlotRow).order_by(TimeSlotRow.sort_order, TimeSlotRow.slot_id)).all():
                    slots[row.slot_id] = TimeSlotView(row.slot_id, row.label, row.period, row.active, row.sort_order)
        except Exception:
            log.exception("list_time_slots failed; falling back to static slots")
    return tuple(sorted(slots.values(), key=lambda item: (item.sort_order, item.slot_id)))


def active_time_slots(*, engine: Engine | None = None) -> tuple[tuple[str, str, str], ...]:
    return tuple((item.slot_id, item.label, item.period) for item in list_time_slots(engine=engine) if item.active)


def upsert_time_slot(
    *,
    slot_id: str,
    label: str,
    period: str,
    active: bool,
    sort_order: int = 100,
    engine: Engine | None = None,
) -> str:
    normalized = _clean_id(slot_id, field="Slot id")
    if not (label or "").strip():
        raise ValueError("Slot label is required")
    db_engine = _require_db_engine(engine)
    with session_scope(db_engine) as session:
        row = session.get(TimeSlotRow, normalized)
        if row is None:
            row = TimeSlotRow(slot_id=normalized)
            session.add(row)
        row.label = label.strip()
        row.period = (period or "").strip()
        row.active = active
        row.sort_order = sort_order
    catalog_cache_clear()
    return normalized


def list_centers(*, engine: Engine | None = None) -> tuple[CenterView, ...]:
    centers = {
        center_id: CenterView(center_id, name, details, True, index)
        for index, (center_id, name, details) in enumerate(CENTERS)
    }
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                for row in session.scalars(select(CenterRow).order_by(CenterRow.sort_order, CenterRow.center_id)).all():
                    centers[row.center_id] = CenterView(row.center_id, row.name, row.details, row.active, row.sort_order)
        except Exception:
            log.exception("list_centers failed; falling back to static centers")
    return tuple(sorted(centers.values(), key=lambda item: (item.sort_order, item.center_id)))


def active_centers(*, engine: Engine | None = None) -> tuple[tuple[str, str, str], ...]:
    return tuple((item.center_id, item.name, item.details) for item in list_centers(engine=engine) if item.active)


def upsert_center(
    *,
    center_id: str,
    name: str,
    details: str,
    active: bool,
    sort_order: int = 100,
    engine: Engine | None = None,
) -> str:
    normalized = _clean_id(center_id, field="Center id")
    if not (name or "").strip():
        raise ValueError("Center name is required")
    db_engine = _require_db_engine(engine)
    with session_scope(db_engine) as session:
        row = session.get(CenterRow, normalized)
        if row is None:
            row = CenterRow(center_id=normalized)
            session.add(row)
        row.name = name.strip()
        row.details = (details or "").strip()
        row.active = active
        row.sort_order = sort_order
    catalog_cache_clear()
    return normalized


def list_text_snippets(*, engine: Engine | None = None) -> tuple[TextSnippetView, ...]:
    snippets = {
        key: TextSnippetView(key=key, title=title, body=body)
        for key, (title, body) in DEFAULT_TEXT_SNIPPETS.items()
    }
    db_engine = _engine_or_configured(engine)
    if db_engine is not None:
        try:
            with session_scope(db_engine) as session:
                for row in session.scalars(select(AdminTextRow).order_by(AdminTextRow.text_key)).all():
                    snippets[row.text_key] = TextSnippetView(key=row.text_key, title=row.title, body=row.body)
        except Exception:
            log.exception("list_text_snippets failed; falling back to defaults")
    return tuple(snippets[key] for key in sorted(snippets))


def upsert_text_snippet(*, key: str, title: str, body: str, engine: Engine | None = None) -> str:
    normalized = (key or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,80}", normalized):
        raise ValueError("Text key must be 2-80 letters, numbers, dots, underscores, or hyphens")
    db_engine = _require_db_engine(engine)
    with session_scope(db_engine) as session:
        row = session.get(AdminTextRow, normalized)
        if row is None:
            row = AdminTextRow(text_key=normalized)
            session.add(row)
        row.title = (title or "").strip() or normalized
        row.body = (body or "").strip()
    catalog_cache_clear()
    return normalized


def normalize_promo_code(text: str) -> str | None:
    """Normalize free-text promo input. Returns the canonical UPPERCASE code
    if valid, else None. Case-insensitive, trims whitespace and stray quotes."""
    if not text:
        return None
    cleaned = _clean_promo_code(text)
    if not re.fullmatch(r"[A-Z0-9_-]{2,40}", cleaned):
        return None
    db_view = _db_promo_view(cleaned)
    if db_view is not None:
        return cleaned if db_view.active else None
    if cleaned in PROMO_CODES:
        return cleaned
    return None


def promo_label(code: str | None) -> str:
    """Human-readable partner label for a normalized code, or ''."""
    cleaned = _clean_promo_code(code or "")
    if not cleaned:
        return ""
    db_view = _db_promo_view(cleaned)
    if db_view is not None:
        return db_view.label if db_view.active else ""
    static_view = _static_promo_view(cleaned)
    return static_view.label if static_view else ""



# ── Closed days (Eids, etc.) ───────────────────────────────────────────────
# ISO dates (YYYY-MM-DD) the shop is closed — skipped when proposing dates.
# Update yearly: Eid dates shift ~10-11 days earlier each year.
CLOSED_DATES: set[str] = {
    "2026-05-27",  # Eid al-Adha 2026 day 1 — CONFIRM closer to the date
    "2026-05-28",  # Eid al-Adha 2026 day 2 — CONFIRM closer to the date
}


# ── Centers ────────────────────────────────────────────────────────────────
# TODO(omar): confirm exact addresses if more centers open later.
CENTERS = [
    ("ctr_casa", "Stand physique", "Mall Triangle Vert, Bouskoura · 7j/7 · 09h-22h30"),
]


# ── Time slots ─────────────────────────────────────────────────────────────
# Lavage jusqu'à 22h (dernier créneau 20h–22h).
SLOTS = [
    ("slot_9_11",   "09h – 11h",  "Matin"),
    ("slot_11_13",  "11h – 13h",  "Fin de matinée"),
    ("slot_14_16",  "14h – 16h",  "Début après-midi"),
    ("slot_16_18",  "16h – 18h",  "Fin d'après-midi"),
    ("slot_18_20",  "18h – 20h",  "Début de soirée"),
    ("slot_20_22",  "20h – 22h",  "Soirée"),
]


# ── Helpers ────────────────────────────────────────────────────────────────
def label_for(pairs, rid: str) -> str:
    """Return the human-readable label (index 1) for a given id."""
    for row in pairs:
        if row[0] == rid:
            return row[1]
    return rid


def build_car_service_rows(
    category: str,
    bucket: str = "all",
    promo_code: str | None = None,
) -> list[tuple[str, str, str]]:
    """Render car services as WhatsApp list rows (id, title, description).

    Title embeds the price for the customer's category inline, e.g.:
      "Le Complet — 125 DH"
    Description is the short feature list from the flyer.

    `bucket` selects which services to show:
      - "wash"       → SERVICES_WASH (L'Extérieur / Le Complet / Le Salon)
      - "detailing"  → SERVICES_DETAILING (Polissage / Céramique / Rénovations / Lustrage)
      - "all"        → both (legacy behaviour, kept for safety)

    `promo_code` (optional, UPPERCASE) swaps in the partner-preferential price
    where a discount row exists. Services not covered by the partner grid keep
    their regular price. Invalid codes are treated as no-promo.

    WhatsApp limits:
      - title ≤ 24 chars (we stay under)
      - description ≤ 72 chars
      - max 10 rows per section → detailing has 7, still well under cap
    """
    if bucket == "wash":
        source = SERVICES_WASH
    elif bucket == "detailing":
        source = SERVICES_DETAILING
    else:
        source = SERVICES_CAR

    rows = []
    normalized_promo = normalize_promo_code(promo_code or "") if promo_code else None
    for sid, name, desc, _prices in source:
        price = service_price(sid, category, promo_code=normalized_promo)
        title = f"{name} — {price} DH" if price is not None else name
        rows.append((sid, title[:24], desc[:72]))
    return rows


def build_moto_service_rows() -> list[tuple[str, str, str]]:
    """Render SERVICES_MOTO as WhatsApp list rows with inline prices."""
    rows = []
    for sid, name, desc, _price in SERVICES_MOTO:
        price = public_service_price(sid, MOTO_PRICE_CATEGORY)
        rows.append((sid, f"{name} — {price} DH"[:24], desc[:72]))
    return rows


def service_price(
    service_id: str,
    category: str,
    promo_code: str | None = None,
) -> int | None:
    """Look up the price for a given service+category. Returns DH integer or None.

    When `promo_code` is a valid UPPERCASE partner code, the partner grid wins
    for any service covered by that partner. Moto is never discounted.
    """
    if category == MOTO_PRICE_CATEGORY:
        return public_service_price(service_id, category)
    normalized_promo = normalize_promo_code(promo_code or "") if promo_code else None
    if normalized_promo:
        db_view = _db_promo_view(normalized_promo)
        promo_view = db_view if db_view is not None else _static_promo_view(normalized_promo)
        if promo_view is not None and promo_view.active:
            promo_price = promo_view.discounts.get((service_id, category))
            if promo_price is not None:
                return promo_price
    return public_service_price(service_id, category)


def service_name(service_id: str) -> str:
    """Short service name (without price), e.g. 'Le Complet'."""
    for sid, name, *_ in SERVICES_CAR:
        if sid == service_id:
            return name
    for sid, name, *_ in SERVICES_MOTO:
        if sid == service_id:
            return name
    return service_id


def service_label(service_id: str, category: str, *, promo_code: str | None = None) -> str:
    """Human-readable service label including price: 'Le Complet — 125 DH'.

    Falls back to the bare service name when no price is found (unknown id or
    category mismatch). Promo discount is applied via service_price().
    """
    name = service_name(service_id)
    price = service_price(service_id, category, promo_code=promo_code)
    if price is None:
        return name
    return f"{name} — {price} DH"


def vehicle_label(category: str, *, make: str | None = None) -> str:
    """Human-readable vehicle label, e.g. 'Citadine (Clio)' or 'Moto/Scooter'.

    `make` is appended in parentheses for car categories only — moto bookings
    don't carry a model field in the bot flow today.
    """
    base = VEHICLE_CATEGORY_LABEL.get(category, category)
    if make and category != MOTO_PRICE_CATEGORY:
        clean = make.strip()
        if clean:
            return f"{base} ({clean})"
    return base


def location_label(location_kind: str, *, center_id: str | None = None) -> str:
    """Human-readable location label, e.g. 'À domicile' or 'Stand <center name>'.

    For `location_kind="center"`, looks up the active centers list. If the
    center name already begins with "Stand" (case-insensitive), it is returned
    as-is to avoid "Stand Stand …" — otherwise the prefix is added.
    """
    if location_kind == "home":
        return "À domicile"
    if location_kind == "center":
        if center_id:
            for cid, name, _details in active_centers():
                if cid == center_id:
                    if name.lower().startswith("stand "):
                        return name
                    return f"Stand {name}"
        return "Au stand"
    return location_kind


def compute_catalog_etag_seed(*, engine: Engine | None = None) -> str:
    """Stable string capturing the catalog's revision.

    Computed from max(updated_at) across all DB-backed catalog tables. Any
    admin edit invalidates the seed (and thus the bootstrap ETag). When the
    DB is unreachable or every table is empty, returns the fixed "static-v1"
    seed so the static catalog still produces a consistent ETag.
    """
    db_engine = _engine_or_configured(engine)
    if db_engine is None:
        return "static-v1"
    try:
        with session_scope(db_engine) as session:
            max_updates: list[str] = []
            for table_cls in (
                ServiceRow,
                ServicePriceRow,
                PromoCodeRow,
                PromoDiscountRow,
                CenterRow,
                TimeSlotRow,
                ClosedDateRow,
                AdminTextRow,
                # Included so /admin/notifications edits invalidate the
                # bootstrap ETag — otherwise the PWA's 24h localStorage
                # bootstrap cache serves a stale staff_contact phone after
                # admins rotate it, and "Parler à l'équipe" deep-links
                # to the old number.
                BookingNotificationSettingRow,
            ):
                max_dt = session.scalar(select(func.max(table_cls.updated_at)))
                if max_dt is not None:
                    max_updates.append(max_dt.isoformat())
        return "|".join(max_updates) if max_updates else "static-v1"
    except Exception:
        log.exception("compute_catalog_etag_seed failed; returning static seed")
        return "static-v1"
