"""add operational service tracking tables

Revision ID: 20260609_0009
Revises: 20260518_0008
Create Date: 2026-06-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260609_0009"
down_revision = "20260518_0008"
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
    if "operational_service_records" in existing:
        return
    op.create_table(
        "operational_service_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("client_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("site_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("source_channel", sa.String(length=40), nullable=False, server_default="whatsapp"),
        sa.Column("source_chat_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("source_message_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("source_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sender_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("vehicle_model", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("matricule", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("prestation", sa.String(length=120), nullable=False, server_default="lavage"),
        sa.Column("unit_price_ht", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="MAD"),
        sa.Column("photo_reference", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="complete"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("unit_price_ht >= 0", name="ck_operational_service_records_unit_price_ht_nonnegative"),
        sa.CheckConstraint(
            "status IN ('complete','missing_matricule','missing_photo','needs_review','voided')",
            name="ck_operational_service_records_status",
        ),
    )
    op.create_index("ix_operational_service_records_service_date", "operational_service_records", ["service_date"])
    op.create_index("ix_operational_service_records_client_name", "operational_service_records", ["client_name"])
    op.create_index("ix_operational_service_records_site_name", "operational_service_records", ["site_name"])
    op.create_index("ix_operational_service_records_source_channel", "operational_service_records", ["source_channel"])
    op.create_index("ix_operational_service_records_source_chat_id", "operational_service_records", ["source_chat_id"])
    op.create_index("ix_operational_service_records_source_message_id", "operational_service_records", ["source_message_id"])
    op.create_index("ix_operational_service_records_source_order", "operational_service_records", ["source_order"])
    op.create_index("ix_operational_service_records_sender_id", "operational_service_records", ["sender_id"])
    op.create_index("ix_operational_service_records_matricule", "operational_service_records", ["matricule"])
    op.create_index("ix_operational_service_records_category", "operational_service_records", ["category"])
    op.create_index("ix_operational_service_records_prestation", "operational_service_records", ["prestation"])
    op.create_index("ix_operational_service_records_status", "operational_service_records", ["status"])
    op.create_index(
        "ix_operational_service_client_site_date",
        "operational_service_records",
        ["client_name", "site_name", "service_date"],
    )
    op.create_index(
        "ix_operational_service_source_message",
        "operational_service_records",
        ["source_channel", "source_message_id"],
    )


def downgrade() -> None:
    existing = _tables()
    if "operational_service_records" in existing:
        op.drop_table("operational_service_records")
