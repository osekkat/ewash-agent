"""Booking record + reference generator."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# In-memory booking counter. For MVP only — resets on Railway redeploy.
# TODO: move to persistent store (SQLite/Postgres) in v0.3.
_counter = 0
_bookings: list[dict] = []


@dataclass
class Booking:
    phone: str
    name: str = ""
    vehicle_type: str = ""      # label, e.g. "A — Citadine" or "🏍️ Moto / Scooter"
    category: str = ""          # pricing key: "A" / "B" / "C" / "MOTO"
    car_model: str = ""         # free text, e.g. "Dacia Logan"
    color: str = ""             # label, e.g. "Blanc"
    service: str = ""           # svc_ext / svc_cpl / svc_sal / svc_pol / svc_scooter / svc_moto
    service_bucket: str = ""    # "wash" or "detailing" (cars only) — which menu the customer came from
    service_label: str = ""     # "Le Complet — 125 DH" (what customer saw)
    price_dh: int = 0           # resolved price in DH at the time of booking
    location_mode: str = ""     # "home" or "center"
    center: str = ""            # ctr_casa / ...
    geo: str = ""               # WhatsApp location pin (home only) — "name | address | 📍 lat, lng"
    address: str = ""           # free-text address + access notes (home only)
    date_label: str = ""        # "Aujourd'hui" / "Demain" / "2026-04-19"
    slot: str = ""              # slot_9_11 / ...
    note: str = ""              # optional customer note
    ref: str = ""               # assigned at confirmation
    created_at: str = ""        # ISO timestamp when confirmed
    addon_service: str = ""        # svc_pol / svc_cer6m / … if customer accepted the -10% Esthétique upsell
    addon_service_label: str = ""  # "Le Polissage — 891 DH (-10%)"
    addon_price_dh: int = 0        # discounted DH price of the addon

    # Promo code (partner preferential tariff, e.g. YASMINE). Empty string
    # means the public / regular grid applies. Captured BEFORE the service
    # menu so the list rows render the correct (discounted) prices.
    promo_code: str = ""            # UPPERCASE canonical code, e.g. "YASMINE"
    promo_label: str = ""           # Human-readable, e.g. "Yasmine Signature"
    price_regular_dh: int = 0       # Public price at time of booking (for savings math)

    # Transient state for the paginated date picker (BOOK_WHEN). Not persisted
    # into `_bookings` in any meaningful way — just survives the round-trip
    # between "Voir plus" taps.
    when_page: int = 0
    when_dates: list[str] = field(default_factory=list)  # ISO dates currently offered

    def assign_ref(self) -> str:
        global _counter
        _counter += 1
        year = datetime.now(timezone.utc).year
        self.ref = f"EW-{year}-{_counter:04d}"
        self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _bookings.append(asdict(self))
        log.info("booking confirmed ref=%s phone=%s payload=%s",
                 self.ref, self.phone, asdict(self))
        return self.ref


def all_bookings() -> list[dict]:
    """For /bookings debug endpoint."""
    return list(_bookings)


def update_booking(ref: str, **fields) -> None:
    """Patch an already-persisted booking in place — used when the customer
    accepts a post-confirmation upsell (e.g. the -10% Esthétique add-on)."""
    for b in _bookings:
        if b.get("ref") == ref:
            b.update(fields)
            log.info("booking updated ref=%s fields=%s", ref, fields)
            return
    log.warning("update_booking: ref=%s not found", ref)
