"""add operational cash ledger tables

Revision ID: 20260518_0008
Revises: 20260516_0007
Create Date: 2026-05-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260518_0008"
down_revision = "20260516_0007"
branch_labels = None
depends_on = None


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _tables() -> set[str]:
    if _is_offline():
        return set()
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()
    if "cash_ledger_entries" not in existing:
        op.create_table(
            "cash_ledger_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_by_phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("direction", sa.String(length=24), nullable=False),
            sa.Column("amount_minor", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="MAD"),
            sa.Column("transaction_type", sa.String(length=60), nullable=False, server_default="unknown"),
            sa.Column("category", sa.String(length=60), nullable=False, server_default="uncategorized"),
            sa.Column("counterparty", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("raw_message_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_channel", sa.String(length=40), nullable=False, server_default="whatsapp"),
            sa.Column("source_message_id", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="recorded"),
            sa.Column("review_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.CheckConstraint("direction IN ('cash_in','cash_out','cash_transfer','cash_adjustment')", name="ck_cash_ledger_entries_direction"),
            sa.CheckConstraint("amount_minor >= 0", name="ck_cash_ledger_entries_amount_nonnegative"),
            sa.CheckConstraint("status IN ('recorded','needs_review','corrected','voided')", name="ck_cash_ledger_entries_status"),
        )
        op.create_index("ix_cash_ledger_entries_owner_phone", "cash_ledger_entries", ["owner_phone"])
        op.create_index("ix_cash_ledger_entries_occurred_at", "cash_ledger_entries", ["occurred_at"])
        op.create_index("ix_cash_ledger_entries_created_by_phone", "cash_ledger_entries", ["created_by_phone"])
        op.create_index("ix_cash_ledger_entries_direction", "cash_ledger_entries", ["direction"])
        op.create_index("ix_cash_ledger_entries_transaction_type", "cash_ledger_entries", ["transaction_type"])
        op.create_index("ix_cash_ledger_entries_category", "cash_ledger_entries", ["category"])
        op.create_index("ix_cash_ledger_entries_source_channel", "cash_ledger_entries", ["source_channel"])
        op.create_index("ix_cash_ledger_entries_source_message_id", "cash_ledger_entries", ["source_message_id"])
        op.create_index("ix_cash_ledger_entries_status", "cash_ledger_entries", ["status"])
        op.create_index("ix_cash_ledger_owner_occurred", "cash_ledger_entries", ["owner_phone", "occurred_at"])

    existing = _tables()
    if "cash_ledger_audit_events" not in existing:
        op.create_table(
            "cash_ledger_audit_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entry_id", sa.Integer(), sa.ForeignKey("cash_ledger_entries.id"), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("actor_phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("before_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("after_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_message_id", sa.String(length=160), nullable=False, server_default=""),
            sa.CheckConstraint("event_type IN ('created','updated','corrected','voided','reviewed')", name="ck_cash_ledger_audit_events_type"),
        )
        op.create_index("ix_cash_ledger_audit_events_entry_id", "cash_ledger_audit_events", ["entry_id"])
        op.create_index("ix_cash_ledger_audit_events_event_type", "cash_ledger_audit_events", ["event_type"])
        op.create_index("ix_cash_ledger_audit_events_actor_phone", "cash_ledger_audit_events", ["actor_phone"])
        op.create_index("ix_cash_ledger_audit_events_occurred_at", "cash_ledger_audit_events", ["occurred_at"])
        op.create_index("ix_cash_ledger_audit_events_source_message_id", "cash_ledger_audit_events", ["source_message_id"])

    existing = _tables()
    if "cash_reconciliations" not in existing:
        op.create_table(
            "cash_reconciliations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_phone", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("business_date", sa.Date(), nullable=False),
            sa.Column("opening_balance_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cash_in_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cash_out_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expected_closing_balance_minor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reported_closing_balance_minor", sa.Integer(), nullable=True),
            sa.Column("difference_minor", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("status IN ('pending','confirmed','discrepancy')", name="ck_cash_reconciliations_status"),
            sa.UniqueConstraint("owner_phone", "business_date", name="uq_cash_reconciliations_owner_business_date"),
        )
        op.create_index("ix_cash_reconciliations_owner_phone", "cash_reconciliations", ["owner_phone"])
        op.create_index("ix_cash_reconciliations_business_date", "cash_reconciliations", ["business_date"])
        op.create_index("ix_cash_reconciliations_status", "cash_reconciliations", ["status"])


def downgrade() -> None:
    existing = _tables()
    if "cash_reconciliations" in existing:
        op.drop_table("cash_reconciliations")
    existing = _tables()
    if "cash_ledger_audit_events" in existing:
        op.drop_table("cash_ledger_audit_events")
    existing = _tables()
    if "cash_ledger_entries" in existing:
        op.drop_table("cash_ledger_entries")
