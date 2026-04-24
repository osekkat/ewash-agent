"""Static catalog for Ewash — matches the Apr 2026 printed tariff sheet.

Pricing categories A/B/C are the industry-standard size tiers Ewash prints
on their own flyer. Moto is a separate lane with its own 2-option service list.
"""

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
    ("svc_cer6m",    "Céramique 6m",        "Protection céramique longue durée (6 mois)",
        {"A": 800, "B": 800,  "C": 800}),
    ("svc_cer6w",    "Céramique 6s",        "Protection céramique express (6 semaines)",
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
    "YASMINE": {
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


def normalize_promo_code(text: str) -> str | None:
    """Normalize free-text promo input. Returns the canonical UPPERCASE code
    if valid, else None. Case-insensitive, trims whitespace and stray quotes."""
    if not text:
        return None
    cleaned = text.strip().strip("'\"“”‘’ ").upper()
    if cleaned in PROMO_CODES:
        return cleaned
    return None


def promo_label(code: str | None) -> str:
    """Human-readable partner label for a normalized code, or ''."""
    if code and code in PROMO_CODES:
        return PROMO_CODES[code]["label"]
    return ""


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

    promo = PROMO_CODES.get(promo_code) if promo_code else None
    rows = []
    for sid, name, desc, prices in source:
        price = prices.get(category)
        if promo:
            promo_price = promo["discounts"].get(sid, {}).get(category)
            if promo_price is not None:
                price = promo_price
        title = f"{name} — {price} DH" if price is not None else name
        rows.append((sid, title[:24], desc[:72]))
    return rows


def build_moto_service_rows() -> list[tuple[str, str, str]]:
    """Render SERVICES_MOTO as WhatsApp list rows with inline prices."""
    rows = []
    for sid, name, desc, price in SERVICES_MOTO:
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
    if category == "MOTO":
        for sid, _name, _desc, price in SERVICES_MOTO:
            if sid == service_id:
                return price
        return None
    if promo_code and promo_code in PROMO_CODES:
        promo_price = PROMO_CODES[promo_code]["discounts"].get(service_id, {}).get(category)
        if promo_price is not None:
            return promo_price
    for sid, _name, _desc, prices in SERVICES_CAR:
        if sid == service_id:
            return prices.get(category)
    return None


def service_name(service_id: str) -> str:
    """Short service name (without price), e.g. 'Le Complet'."""
    for sid, name, *_ in SERVICES_CAR:
        if sid == service_id:
            return name
    for sid, name, *_ in SERVICES_MOTO:
        if sid == service_id:
            return name
    return service_id
