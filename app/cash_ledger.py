"""Operational cash ledger for Omar's WhatsApp-first cash tracking.

The ledger is intentionally operational rather than accounting-grade, but every
mutation writes an audit event so the data model can graduate toward formal
accounting later without losing correction history.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CashLedgerAuditEventRow, CashLedgerEntryRow, utcnow

CASH_LEDGER_CATEGORIES = {
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
    "uncategorized",
}

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fuel", ("fuel", "gas", "essence", "gasoil", "diesel", "carburant")),
    ("parking", ("parking", "park", "stationnement")),
    ("tolls", ("toll", "péage", "peage")),
    ("cleaning_products", ("cleaning", "detergent", "produit", "shampoo", "savon")),
    ("vehicle_maintenance", ("maintenance", "repair", "réparation", "reparation", "garage", "pneu")),
    ("equipment", ("equipment", "matériel", "materiel", "machine")),
    ("supplies", ("supplies", "towels", "serviettes", "microfibre", "achat")),
    ("meals", ("meal", "lunch", "dinner", "coffee", "déjeuner", "dejeuner", "café", "cafe")),
    ("transport", ("taxi", "transport", "train", "bus")),
    ("staff_labor", ("staff", "washer", "laveur", "employee", "employé", "employe", "labor", "main d")),
)

_CASH_IN_WORDS = (
    "withdrew", "withdraw", "retiré", "retire", "retir", "cash out from bank",
    "received", "reçu", "recu", "collected", "encaissé", "encaisse",
)
_CASH_OUT_WORDS = (
    "paid", "payé", "paye", "bought", "buy", "spent", "gave", "donné", "donne",
    "deposit", "deposited", "déposé", "depose", "versé", "verse",
)
_BANK_WITHDRAWAL_WORDS = ("withdrew", "withdraw", "retiré", "retire", "retir")
_BANK_DEPOSIT_WORDS = ("deposit", "deposited", "déposé", "depose", "versé", "verse")

_AMOUNT_RE = re.compile(
    r"(?<!\w)(\d{1,3}(?:[\s,._]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:mad|dh|dhs|dirham|dirhams)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CashLedgerIntent:
    direction: str
    amount_minor: int
    currency: str = "MAD"
    transaction_type: str = "unknown"
    category: str = "uncategorized"
    counterparty: str = ""
    description: str = ""
    confidence: float = 0.0
    review_reason: str = ""


@dataclass(frozen=True)
class CashLedgerEntryInput:
    direction: str
    amount_minor: int
    owner_phone: str
    created_by_phone: str
    occurred_at: datetime | None = None
    currency: str = "MAD"
    transaction_type: str = "unknown"
    category: str = "uncategorized"
    counterparty: str = ""
    description: str = ""
    raw_message_text: str = ""
    source_channel: str = "whatsapp"
    source_message_id: str = ""
    confidence: float = 0.0
    status: str = "recorded"
    review_reason: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class CashDaySummary:
    owner_phone: str
    business_date: date
    cash_in_minor: int
    cash_out_minor: int
    expected_cash_on_hand_minor: int
    needs_review_count: int


def _parse_amount_minor(text: str) -> int | None:
    match = _AMOUNT_RE.search(text or "")
    if not match:
        return None
    whole = re.sub(r"[\s,._]", "", match.group(1))
    cents = (match.group(2) or "").ljust(2, "0")[:2]
    return int(whole) * 100 + (int(cents) if cents else 0)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _infer_category(text: str, direction: str, transaction_type: str) -> str:
    if transaction_type in {"bank_withdrawal", "bank_deposit"}:
        return "bank"
    if transaction_type == "cash_received":
        return "customer_payment"
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "uncategorized" if direction == "cash_out" else "other_cash_in"


def _extract_counterparty(text: str) -> str:
    # Natural WhatsApp messages often look like "paid Hamza 300" or
    # "paid 300 to Hamza". Keep this deterministic; an LLM can replace it later.
    patterns = (
        r"\b(?:paid|payé|paye|gave|donné|donne)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ' -]{1,60}?)\s+\d",
        r"\b(?:paid|payé|paye|gave|donné|donne)\s+\d[\d\s,._]*(?:\s*(?:mad|dh|dhs|dirhams?))?\s+(?:to|à|a)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ' -]{1,60})",
        r"\b(?:from|de)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ' -]{1,60})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;-")
            # Strip common trailing purpose markers.
            value = re.split(r"\s+(?:for|pour)\s+", value, maxsplit=1, flags=re.IGNORECASE)[0]
            if value:
                return value[:160]
    return ""


def parse_cash_ledger_intent(text: str) -> CashLedgerIntent | None:
    """Return a structured transaction intent, or None for unrelated text."""
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.casefold()
    amount_minor = _parse_amount_minor(raw)
    if amount_minor is None:
        return None

    if _contains_any(lowered, _BANK_WITHDRAWAL_WORDS):
        direction = "cash_in"
        transaction_type = "bank_withdrawal"
    elif _contains_any(lowered, _BANK_DEPOSIT_WORDS):
        direction = "cash_out"
        transaction_type = "bank_deposit"
    elif _contains_any(lowered, ("received", "reçu", "recu", "collected", "encaissé", "encaisse")):
        direction = "cash_in"
        transaction_type = "cash_received"
    elif _contains_any(lowered, _CASH_OUT_WORDS):
        direction = "cash_out"
        transaction_type = "cash_payment"
    elif _contains_any(lowered, _CASH_IN_WORDS):
        direction = "cash_in"
        transaction_type = "other_cash_in"
    else:
        return None

    category = _infer_category(lowered, direction, transaction_type)
    counterparty = "bank" if transaction_type in {"bank_withdrawal", "bank_deposit"} else _extract_counterparty(raw)
    confidence = 0.9 if category != "uncategorized" or transaction_type.startswith("bank_") else 0.72
    review_reason = "category_unclear" if category == "uncategorized" else ""
    return CashLedgerIntent(
        direction=direction,
        amount_minor=amount_minor,
        transaction_type=transaction_type,
        category=category,
        counterparty=counterparty,
        description=raw,
        confidence=confidence,
        review_reason=review_reason,
    )


def _entry_snapshot(entry: CashLedgerEntryRow) -> dict[str, Any]:
    return {
        "id": entry.id,
        "owner_phone": entry.owner_phone,
        "direction": entry.direction,
        "amount_minor": entry.amount_minor,
        "currency": entry.currency,
        "transaction_type": entry.transaction_type,
        "category": entry.category,
        "counterparty": entry.counterparty,
        "description": entry.description,
        "status": entry.status,
        "review_reason": entry.review_reason,
    }


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _add_audit_event(
    session: Session,
    *,
    entry: CashLedgerEntryRow,
    event_type: str,
    actor_phone: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str = "",
    source_message_id: str = "",
) -> None:
    session.add(
        CashLedgerAuditEventRow(
            entry=entry,
            event_type=event_type,
            actor_phone=actor_phone,
            before_json=_json(before or {}),
            after_json=_json(after or {}),
            reason=reason,
            source_message_id=source_message_id,
        )
    )


def create_cash_ledger_entry(session: Session, data: CashLedgerEntryInput) -> CashLedgerEntryRow:
    if data.amount_minor < 0:
        raise ValueError("amount_minor must be non-negative")
    if data.direction not in {"cash_in", "cash_out", "cash_transfer", "cash_adjustment"}:
        raise ValueError(f"unknown cash ledger direction: {data.direction}")
    category = data.category if data.category in CASH_LEDGER_CATEGORIES else "uncategorized"
    review_reason = data.review_reason
    status = data.status
    if category == "uncategorized" and status == "recorded":
        status = "needs_review"
        review_reason = review_reason or "category_unclear"

    entry = CashLedgerEntryRow(
        owner_phone=data.owner_phone,
        occurred_at=data.occurred_at or utcnow(),
        created_by_phone=data.created_by_phone,
        direction=data.direction,
        amount_minor=data.amount_minor,
        currency=data.currency or "MAD",
        transaction_type=data.transaction_type or "unknown",
        category=category,
        counterparty=data.counterparty or "",
        description=data.description or "",
        raw_message_text=data.raw_message_text or "",
        source_channel=data.source_channel or "whatsapp",
        source_message_id=data.source_message_id or "",
        confidence=max(0.0, min(float(data.confidence or 0.0), 1.0)),
        status=status,
        review_reason=review_reason or "",
        metadata_json=_json(data.metadata or {}),
    )
    session.add(entry)
    session.flush()
    _add_audit_event(
        session,
        entry=entry,
        event_type="created",
        actor_phone=data.created_by_phone,
        before=None,
        after=_entry_snapshot(entry),
        source_message_id=data.source_message_id,
    )
    return entry


def correct_cash_ledger_entry(
    session: Session,
    *,
    entry_id: int,
    actor_phone: str,
    reason: str,
    **changes: Any,
) -> CashLedgerEntryRow:
    entry = session.get(CashLedgerEntryRow, entry_id)
    if entry is None:
        raise ValueError(f"cash ledger entry not found: {entry_id}")
    before = _entry_snapshot(entry)
    allowed = {"amount_minor", "category", "counterparty", "description", "transaction_type", "direction"}
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError(f"field cannot be corrected: {key}")
        setattr(entry, key, value)
    if entry.category not in CASH_LEDGER_CATEGORIES:
        entry.category = "uncategorized"
    entry.status = "corrected"
    entry.review_reason = "" if entry.category != "uncategorized" else "category_unclear"
    entry.updated_at = utcnow()
    session.flush()
    _add_audit_event(
        session,
        entry=entry,
        event_type="corrected",
        actor_phone=actor_phone,
        before=before,
        after=_entry_snapshot(entry),
        reason=reason,
    )
    return entry


def review_cash_ledger_category(
    session: Session,
    *,
    entry_id: int,
    actor_phone: str,
    category: str,
    reason: str = "admin category update",
) -> CashLedgerEntryRow:
    """Update a ledger entry category from the dashboard and audit the review.

    Category cleanup should resolve the normal `uncategorized` review state
    without marking the financial transaction itself as corrected.
    """
    if category not in CASH_LEDGER_CATEGORIES:
        raise ValueError(f"unknown cash ledger category: {category}")
    entry = session.get(CashLedgerEntryRow, entry_id)
    if entry is None:
        raise ValueError(f"cash ledger entry not found: {entry_id}")
    if entry.status == "voided":
        raise ValueError("voided cash ledger entries cannot be reviewed")

    before = _entry_snapshot(entry)
    entry.category = category
    if category == "uncategorized":
        entry.status = "needs_review"
        entry.review_reason = entry.review_reason or "category_unclear"
    else:
        entry.status = "recorded"
        entry.review_reason = ""
    entry.updated_at = utcnow()
    session.flush()
    _add_audit_event(
        session,
        entry=entry,
        event_type="reviewed",
        actor_phone=actor_phone,
        before=before,
        after=_entry_snapshot(entry),
        reason=reason,
    )
    return entry


def expected_cash_balance_minor(session: Session, *, owner_phone: str) -> int:
    rows = session.execute(
        select(CashLedgerEntryRow.direction, func.coalesce(func.sum(CashLedgerEntryRow.amount_minor), 0))
        .where(CashLedgerEntryRow.owner_phone == owner_phone, CashLedgerEntryRow.status != "voided")
        .group_by(CashLedgerEntryRow.direction)
    ).all()
    totals = {direction: int(total or 0) for direction, total in rows}
    return totals.get("cash_in", 0) - totals.get("cash_out", 0) + totals.get("cash_adjustment", 0)


def cash_summary_for_day(session: Session, *, owner_phone: str, business_date: date) -> CashDaySummary:
    start = datetime.combine(business_date, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    rows = session.execute(
        select(CashLedgerEntryRow.direction, func.coalesce(func.sum(CashLedgerEntryRow.amount_minor), 0))
        .where(
            CashLedgerEntryRow.owner_phone == owner_phone,
            CashLedgerEntryRow.status != "voided",
            CashLedgerEntryRow.occurred_at >= start,
            CashLedgerEntryRow.occurred_at < end,
        )
        .group_by(CashLedgerEntryRow.direction)
    ).all()
    totals = {direction: int(total or 0) for direction, total in rows}
    needs_review_count = int(
        session.scalar(
            select(func.count(CashLedgerEntryRow.id)).where(
                CashLedgerEntryRow.owner_phone == owner_phone,
                CashLedgerEntryRow.status == "needs_review",
                CashLedgerEntryRow.occurred_at >= start,
                CashLedgerEntryRow.occurred_at < end,
            )
        )
        or 0
    )
    return CashDaySummary(
        owner_phone=owner_phone,
        business_date=business_date,
        cash_in_minor=totals.get("cash_in", 0),
        cash_out_minor=totals.get("cash_out", 0),
        expected_cash_on_hand_minor=expected_cash_balance_minor(session, owner_phone=owner_phone),
        needs_review_count=needs_review_count,
    )


def entry_input_from_intent(
    intent: CashLedgerIntent,
    *,
    owner_phone: str,
    created_by_phone: str,
    raw_message_text: str,
    source_message_id: str = "",
) -> CashLedgerEntryInput:
    return CashLedgerEntryInput(
        owner_phone=owner_phone,
        created_by_phone=created_by_phone,
        direction=intent.direction,
        amount_minor=intent.amount_minor,
        currency=intent.currency,
        transaction_type=intent.transaction_type,
        category=intent.category,
        counterparty=intent.counterparty,
        description=intent.description,
        raw_message_text=raw_message_text,
        source_message_id=source_message_id,
        confidence=intent.confidence,
        review_reason=intent.review_reason,
        metadata={"parser": "deterministic_v1", "intent": asdict(intent)},
    )
