"""In-memory conversation state keyed by phone number.

State machine:
  IDLE → MENU → (book path | services info | handoff)
  BOOK_NAME → BOOK_VEHICLE → BOOK_MODEL → BOOK_COLOR
  → BOOK_WHERE → (BOOK_GEO → BOOK_ADDRESS  ← home: pin first, then address+notes
                  | BOOK_CENTER)
  → BOOK_PROMO_ASK → (BOOK_PROMO_CODE)?    ← cars only: partner promo opt-in
  → BOOK_SERVICE (Lavages catalog — Esthétique is removed from the main flow
                  and offered as a post-confirmation -10% upsell instead;
                  list rows reflect the promo grid when a code was applied)
  → BOOK_WHEN → BOOK_SLOT → BOOK_NOTE → (BOOK_NOTE_TEXT)?
  → BOOK_CONFIRM
  → UPSELL_DETAILING (🎁 offer -10% Esthétique)        ← cars only
  → (UPSELL_DETAILING_PICK)? → DONE

Moto lane: goes from BOOK_VEHICLE → BOOK_WHERE → BOOK_SERVICE (single menu).
It SKIPS the promo prompt (the flyers show no moto discount on any partner
tier) AND the UPSELL_DETAILING step (no Esthétique catalog for 2-wheels).

Rationale for BOOK_WHERE before BOOK_SERVICE: the customer commits to
home-vs-stand before seeing prices, and the service prompt can hint the
chosen lieu in its header. The promo prompt sits between location and the
service menu so the list rows can render discounted prices directly.

On any unexpected input we gracefully re-prompt the current step.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .booking import Booking

log = logging.getLogger(__name__)

# TTL for stale conversations (seconds). After this, we reset to IDLE.
STATE_TTL = 60 * 60 * 2  # 2h


@dataclass
class Session:
    state: str = "IDLE"
    booking: Optional[Booking] = None
    last_seen: float = field(default_factory=time.time)


_sessions: dict[str, Session] = {}


def get(phone: str) -> Session:
    s = _sessions.get(phone)
    now = time.time()
    if s is None or (now - s.last_seen) > STATE_TTL:
        s = Session()
        _sessions[phone] = s
    s.last_seen = now
    return s


def reset(phone: str) -> None:
    _sessions[phone] = Session()


def start_booking(phone: str) -> Session:
    s = get(phone)
    s.state = "BOOK_NAME"
    s.booking = Booking(phone=phone)
    return s
