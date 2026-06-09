"""Agent payroll/attendance tracking imported from Omar's legacy CSV notes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AdminTextRow, AgentPayrollEventRow

AGENT_PAYROLL_MANUAL_KEY = "agent_payroll.manual"

_FIELD_MAP = {
    "Date": "event_date",
    "Agent": "agent_name",
    "Type": "event_type",
    "Prévenu ?": "warned",
    "Heure début prévue": "scheduled_start_time",
    "Heure fin prévue": "scheduled_end_time",
    "Heure réelle début": "actual_start_time",
    "Heure réelle fin": "actual_end_time",
    "Durée impactée (h)": "impacted_hours",
    "Justificatif": "justification",
    "Commentaire": "comment",
    "Validé par": "validated_by",
    "Impact paie (MAD)": "payroll_impact_dh",
    "Statut": "status",
}


@dataclass(frozen=True)
class AgentPayrollEventInput:
    event_date: date
    agent_name: str
    event_type: str
    warned: str = ""
    scheduled_start_time: str = ""
    scheduled_end_time: str = ""
    actual_start_time: str = ""
    actual_end_time: str = ""
    impacted_hours: float | None = None
    justification: str = ""
    comment: str = ""
    validated_by: str = ""
    payroll_impact_dh: int | None = None
    status: str = "À vérifier"
    source_file: str = ""
    source_row_number: int = 0
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentPayrollImportResult:
    created_count: int
    skipped_count: int
    row_ids: list[int]


def _clean(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _parse_float(value: object) -> float | None:
    text = _clean(value, limit=40).replace(",", ".")
    if not text:
        return None
    return float(text)


def _parse_int(value: object) -> int | None:
    text = _clean(value, limit=40).replace(" ", "").replace(",", ".")
    if not text:
        return None
    return int(float(text))


def _parse_date(value: object) -> date:
    text = _clean(value, limit=40)
    if not text:
        raise ValueError("Date is required")
    return date.fromisoformat(text)


def agent_payroll_input_from_csv_row(
    row: dict[str, str],
    *,
    source_file: str,
    source_row_number: int,
) -> AgentPayrollEventInput:
    mapped = {_FIELD_MAP[key]: value for key, value in row.items() if key in _FIELD_MAP}
    event_date = _parse_date(mapped.get("event_date"))
    agent_name = _clean(mapped.get("agent_name"), limit=120)
    event_type = _clean(mapped.get("event_type"), limit=120)
    if not agent_name:
        raise ValueError(f"Agent is required at row {source_row_number}")
    if not event_type:
        raise ValueError(f"Type is required at row {source_row_number}")

    return AgentPayrollEventInput(
        event_date=event_date,
        agent_name=agent_name,
        event_type=event_type,
        warned=_clean(mapped.get("warned"), limit=16),
        scheduled_start_time=_clean(mapped.get("scheduled_start_time"), limit=16),
        scheduled_end_time=_clean(mapped.get("scheduled_end_time"), limit=16),
        actual_start_time=_clean(mapped.get("actual_start_time"), limit=16),
        actual_end_time=_clean(mapped.get("actual_end_time"), limit=16),
        impacted_hours=_parse_float(mapped.get("impacted_hours")),
        justification=_clean(mapped.get("justification"), limit=500),
        comment=_clean(mapped.get("comment"), limit=1000),
        validated_by=_clean(mapped.get("validated_by"), limit=120),
        payroll_impact_dh=_parse_int(mapped.get("payroll_impact_dh")),
        status=_clean(mapped.get("status"), limit=40) or "À vérifier",
        source_file=source_file,
        source_row_number=source_row_number,
        raw_payload={key: value for key, value in row.items() if key},
    )


def create_agent_payroll_event(session: Session, data: AgentPayrollEventInput) -> AgentPayrollEventRow:
    existing = session.scalars(
        select(AgentPayrollEventRow).where(
            AgentPayrollEventRow.source_file == data.source_file,
            AgentPayrollEventRow.source_row_number == data.source_row_number,
        )
    ).first()
    if existing is not None:
        return existing

    row = AgentPayrollEventRow(
        event_date=data.event_date,
        agent_name=_clean(data.agent_name, limit=120),
        event_type=_clean(data.event_type, limit=120),
        warned=_clean(data.warned, limit=16),
        scheduled_start_time=_clean(data.scheduled_start_time, limit=16),
        scheduled_end_time=_clean(data.scheduled_end_time, limit=16),
        actual_start_time=_clean(data.actual_start_time, limit=16),
        actual_end_time=_clean(data.actual_end_time, limit=16),
        impacted_hours=data.impacted_hours,
        justification=_clean(data.justification, limit=500),
        comment=_clean(data.comment, limit=1000),
        validated_by=_clean(data.validated_by, limit=120),
        payroll_impact_dh=data.payroll_impact_dh,
        status=_clean(data.status, limit=40) or "À vérifier",
        source_file=_clean(data.source_file, limit=240),
        source_row_number=int(data.source_row_number or 0),
        raw_payload_json=json.dumps(data.raw_payload or {}, ensure_ascii=False),
    )
    session.add(row)
    session.flush()
    return row


def import_agent_payroll_csv(session: Session, path: str | Path) -> AgentPayrollImportResult:
    csv_path = Path(path)
    source_file = str(csv_path)
    created = 0
    skipped = 0
    row_ids: list[int] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for source_row_number, raw_row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in raw_row.values()):
                continue
            existing = session.scalars(
                select(AgentPayrollEventRow).where(
                    AgentPayrollEventRow.source_file == source_file,
                    AgentPayrollEventRow.source_row_number == source_row_number,
                )
            ).first()
            if existing is not None:
                skipped += 1
                row_ids.append(existing.id)
                continue
            row = create_agent_payroll_event(
                session,
                agent_payroll_input_from_csv_row(
                    raw_row,
                    source_file=source_file,
                    source_row_number=source_row_number,
                ),
            )
            created += 1
            row_ids.append(row.id)
    return AgentPayrollImportResult(created_count=created, skipped_count=skipped, row_ids=row_ids)


def upsert_agent_payroll_manual(session: Session, path: str | Path) -> AdminTextRow:
    text_path = Path(path)
    body = text_path.read_text(encoding="utf-8-sig")
    row = session.get(AdminTextRow, AGENT_PAYROLL_MANUAL_KEY)
    if row is None:
        row = AdminTextRow(text_key=AGENT_PAYROLL_MANUAL_KEY)
        session.add(row)
    row.title = "Mode d’emploi — Suivi agents / paie"
    row.body = body
    session.flush()
    return row
