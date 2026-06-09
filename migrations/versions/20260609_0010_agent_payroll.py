"""add agent payroll tracking tables

Revision ID: 20260609_0010
Revises: 20260609_0009
Create Date: 2026-06-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260609_0010"
down_revision = "20260609_0009"
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
    if "agent_payroll_events" in existing:
        return
    op.create_table(
        "agent_payroll_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("warned", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("scheduled_start_time", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("scheduled_end_time", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("actual_start_time", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("actual_end_time", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("impacted_hours", sa.Float(), nullable=True),
        sa.Column("justification", sa.Text(), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("validated_by", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("payroll_impact_dh", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="À vérifier"),
        sa.Column("source_file", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("source_row_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("impacted_hours IS NULL OR impacted_hours >= 0", name="ck_agent_payroll_impacted_hours_nonnegative"),
        sa.UniqueConstraint("source_file", "source_row_number", name="uq_agent_payroll_source_row"),
    )
    op.create_index("ix_agent_payroll_events_event_date", "agent_payroll_events", ["event_date"])
    op.create_index("ix_agent_payroll_events_agent_name", "agent_payroll_events", ["agent_name"])
    op.create_index("ix_agent_payroll_events_event_type", "agent_payroll_events", ["event_type"])
    op.create_index("ix_agent_payroll_events_status", "agent_payroll_events", ["status"])
    op.create_index("ix_agent_payroll_events_source_file", "agent_payroll_events", ["source_file"])
    op.create_index("ix_agent_payroll_events_source_row_number", "agent_payroll_events", ["source_row_number"])
    op.create_index("ix_agent_payroll_event_date_agent", "agent_payroll_events", ["event_date", "agent_name"])


def downgrade() -> None:
    existing = _tables()
    if "agent_payroll_events" in existing:
        op.drop_table("agent_payroll_events")
