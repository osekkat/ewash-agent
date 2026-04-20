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
# Each row: (id, short_name, description ≤72 chars, prices_dict).
# prices_dict keys are category letters A/B/C.
SERVICES_CAR = [
    ("svc_ext",  "L'Extérieur",  "Carrosserie, vitres, jantes + wax 1 semaine",
        {"A": 60,  "B": 65,   "C": 70}),
    ("svc_cpl",  "Le Complet",   "L'Extérieur + intérieur + aspirateur tapis/sièges",
        {"A": 115, "B": 125,  "C": 135}),
    ("svc_sal",  "Le Salon",     "Le Complet + injection/extraction sièges et tissus",
        {"A": 490, "B": 540,  "C": 590}),
    ("svc_pol",  "Le Polissage", "Rénov. carrosserie + protection hydrophobe 4 sem.",
        {"A": 990, "B": 1070, "C": 1150}),
]

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


# ── Centers ────────────────────────────────────────────────────────────────
# TODO(omar): confirm exact addresses if more centers open later.
CENTERS = [
    ("ctr_casa", "Centre Ewash Casablanca", "Adresse à confirmer"),
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


def build_car_service_rows(category: str) -> list[tuple[str, str, str]]:
    """Render SERVICES_CAR as WhatsApp list rows (id, title, description).

    Title embeds the price for the customer's category inline, e.g.:
      "Le Complet — 125 DH"
    Description is the short feature list from the flyer.

    WhatsApp limits:
      - title ≤ 24 chars (we stay under)
      - description ≤ 72 chars
    """
    rows = []
    for sid, name, desc, prices in SERVICES_CAR:
        price = prices.get(category)
        title = f"{name} — {price} DH" if price is not None else name
        rows.append((sid, title[:24], desc[:72]))
    return rows


def build_moto_service_rows() -> list[tuple[str, str, str]]:
    """Render SERVICES_MOTO as WhatsApp list rows with inline prices."""
    rows = []
    for sid, name, desc, price in SERVICES_MOTO:
        rows.append((sid, f"{name} — {price} DH"[:24], desc[:72]))
    return rows


def service_price(service_id: str, category: str) -> int | None:
    """Look up the price for a given service+category. Returns DH integer or None."""
    if category == "MOTO":
        for sid, _name, _desc, price in SERVICES_MOTO:
            if sid == service_id:
                return price
        return None
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
