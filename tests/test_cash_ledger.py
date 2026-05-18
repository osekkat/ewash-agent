from datetime import date, datetime, timezone

from app.cash_ledger import (
    CashLedgerEntryInput,
    cash_summary_for_day,
    correct_cash_ledger_entry,
    create_cash_ledger_entry,
    expected_cash_balance_minor,
    parse_cash_ledger_intent,
)
from app.db import init_db, make_engine, session_scope
from app.models import CashLedgerAuditEventRow, CashLedgerEntryRow


def test_parse_cash_ledger_intent_detects_cash_in_withdrawal():
    intent = parse_cash_ledger_intent("I just withdrew 2,000 MAD from the bank")

    assert intent is not None
    assert intent.direction == "cash_in"
    assert intent.transaction_type == "bank_withdrawal"
    assert intent.amount_minor == 200_000
    assert intent.currency == "MAD"
    assert intent.confidence >= 0.8


def test_parse_cash_ledger_intent_detects_cash_out_payment_category_and_counterparty():
    intent = parse_cash_ledger_intent("I paid Hamza 300 MAD for fuel")

    assert intent is not None
    assert intent.direction == "cash_out"
    assert intent.transaction_type == "cash_payment"
    assert intent.amount_minor == 30_000
    assert intent.category == "fuel"
    assert intent.counterparty == "Hamza"


def test_parse_cash_ledger_intent_ignores_non_transaction_text():
    assert parse_cash_ledger_intent("what time is my next meeting?") is None


def test_create_cash_ledger_entry_records_audit_and_updates_expected_balance():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    occurred_at = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)

    with session_scope(engine) as session:
        create_cash_ledger_entry(
            session,
            CashLedgerEntryInput(
                occurred_at=occurred_at,
                owner_phone="212688636993",
                created_by_phone="212688636993",
                direction="cash_in",
                amount_minor=200_000,
                transaction_type="bank_withdrawal",
                category="bank",
                counterparty="bank",
                raw_message_text="I withdrew 2000 MAD",
                source_message_id="wamid.1",
                confidence=0.95,
            ),
        )
        create_cash_ledger_entry(
            session,
            CashLedgerEntryInput(
                occurred_at=occurred_at,
                owner_phone="212688636993",
                created_by_phone="212688636993",
                direction="cash_out",
                amount_minor=30_000,
                transaction_type="cash_payment",
                category="staff_labor",
                counterparty="Hamza",
                raw_message_text="I paid Hamza 300 MAD",
                source_message_id="wamid.2",
                confidence=0.85,
            ),
        )

        assert expected_cash_balance_minor(session, owner_phone="212688636993") == 170_000
        summary = cash_summary_for_day(session, owner_phone="212688636993", business_date=date(2026, 5, 18))
        assert summary.cash_in_minor == 200_000
        assert summary.cash_out_minor == 30_000
        assert summary.expected_cash_on_hand_minor == 170_000
        assert summary.needs_review_count == 0
        assert session.query(CashLedgerAuditEventRow).count() == 2


def test_correct_cash_ledger_entry_preserves_audit_history():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        entry = create_cash_ledger_entry(
            session,
            CashLedgerEntryInput(
                owner_phone="212688636993",
                created_by_phone="212688636993",
                direction="cash_out",
                amount_minor=10_000,
                transaction_type="cash_payment",
                category="uncategorized",
                counterparty="Mohamed",
                raw_message_text="I paid Mohamed 100",
            ),
        )
        entry_id = entry.id

    with session_scope(engine) as session:
        corrected = correct_cash_ledger_entry(
            session,
            entry_id=entry_id,
            actor_phone="212688636993",
            reason="Actually it was 120",
            amount_minor=12_000,
            category="parking",
        )
        assert corrected.amount_minor == 12_000
        assert corrected.category == "parking"
        assert corrected.status == "corrected"

        events = session.query(CashLedgerAuditEventRow).order_by(CashLedgerAuditEventRow.id).all()
        assert [event.event_type for event in events] == ["created", "corrected"]
        assert '"amount_minor": 10000' in events[1].before_json
        assert '"amount_minor": 12000' in events[1].after_json
        assert session.query(CashLedgerEntryRow).count() == 1
