from pathlib import Path

from sqlalchemy import select

from app.agent_payroll import (
    AGENT_PAYROLL_MANUAL_KEY,
    import_agent_payroll_csv,
    upsert_agent_payroll_manual,
)
from app.db import init_db, make_engine, session_scope
from app.models import AdminTextRow, AgentPayrollEventRow


CSV_CONTENT = """Date;Agent;Type;Prévenu ?;Heure début prévue;Heure fin prévue;Heure réelle début;Heure réelle fin;Durée impactée (h);Justificatif;Commentaire;Validé par;Impact paie (MAD);Statut
2026-06-02;Exemple agent;Absence non justifiée;Non;09:00;18:00;;;8;Aucun;Absent sans prévenir;;0;À vérifier
2026-06-02;Yazid;Absence non justifiée;Non;;;;;;Aucun;N’est pas allé travailler cet après-midi et n’a pas prévenu. Saisi depuis vocal WhatsApp.;;;À vérifier
"""

MANUAL_CONTENT = """MODE D’EMPLOI - Suivi absences / retards / incidents agents

Objectif : suivre les absences, retards et incidents pour préparer la fiche de paie mensuelle.
"""


def test_import_agent_payroll_csv_preserves_all_rows_and_fields(tmp_path):
    csv_path = tmp_path / "suivi_absences_agents_paie.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        result = import_agent_payroll_csv(session, csv_path)

    assert result.created_count == 2
    assert result.skipped_count == 0
    with session_scope(engine) as session:
        rows = session.scalars(select(AgentPayrollEventRow).order_by(AgentPayrollEventRow.source_row_number)).all()
        assert [row.agent_name for row in rows] == ["Exemple agent", "Yazid"]
        assert rows[0].event_date.isoformat() == "2026-06-02"
        assert rows[0].event_type == "Absence non justifiée"
        assert rows[0].warned == "Non"
        assert rows[0].scheduled_start_time == "09:00"
        assert rows[0].scheduled_end_time == "18:00"
        assert rows[0].impacted_hours == 8.0
        assert rows[0].payroll_impact_dh == 0
        assert rows[0].status == "À vérifier"
        assert rows[1].agent_name == "Yazid"
        assert rows[1].impacted_hours is None
        assert rows[1].payroll_impact_dh is None
        assert "vocal WhatsApp" in rows[1].comment
        assert rows[1].raw_payload_json.startswith("{")


def test_import_agent_payroll_csv_is_idempotent_by_source_file_and_row(tmp_path):
    csv_path = tmp_path / "suivi_absences_agents_paie.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        first = import_agent_payroll_csv(session, csv_path)
        second = import_agent_payroll_csv(session, csv_path)

    assert first.created_count == 2
    assert second.created_count == 0
    assert second.skipped_count == 2
    with session_scope(engine) as session:
        assert session.query(AgentPayrollEventRow).count() == 2


def test_upsert_agent_payroll_manual_stores_txt_file_in_admin_texts(tmp_path):
    manual_path = tmp_path / "mode_emploi_suivi_absences_agents_paie.txt"
    manual_path.write_text(MANUAL_CONTENT, encoding="utf-8")
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        row = upsert_agent_payroll_manual(session, manual_path)

    assert row.text_key == AGENT_PAYROLL_MANUAL_KEY
    with session_scope(engine) as session:
        stored = session.get(AdminTextRow, AGENT_PAYROLL_MANUAL_KEY)
        assert stored is not None
        assert stored.title == "Mode d’emploi — Suivi agents / paie"
        assert "Objectif : suivre les absences" in stored.body
