from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin
from app.agent_payroll import import_agent_payroll_csv, upsert_agent_payroll_manual
from app.config import settings
from app.db import init_db, make_engine, session_scope


CSV_CONTENT = """Date;Agent;Type;Prévenu ?;Heure début prévue;Heure fin prévue;Heure réelle début;Heure réelle fin;Durée impactée (h);Justificatif;Commentaire;Validé par;Impact paie (MAD);Statut
2026-06-02;Exemple agent;Absence non justifiée;Non;09:00;18:00;;;8;Aucun;Absent sans prévenir;;0;À vérifier
2026-06-02;Yazid;Absence non justifiée;Non;;;;;;Aucun;N’est pas allé travailler cet après-midi et n’a pas prévenu. Saisi depuis vocal WhatsApp.;;;À vérifier
"""

MANUAL_CONTENT = """MODE D’EMPLOI - Suivi absences / retards / incidents agents

Objectif : suivre les absences, retards et incidents pour préparer la fiche de paie mensuelle.
"""


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post("/admin", data={"password": settings.admin_password}, follow_redirects=False)
    assert response.status_code == 303


def test_admin_payroll_page_shows_imported_csv_rows_and_manual(monkeypatch, tmp_path):
    db_path = tmp_path / "agent-payroll-admin.sqlite3"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    init_db(engine)
    monkeypatch.setattr(admin, "_configured_engine", lambda: engine)
    csv_path = tmp_path / "suivi_absences_agents_paie.csv"
    manual_path = tmp_path / "mode_emploi_suivi_absences_agents_paie.txt"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")
    manual_path.write_text(MANUAL_CONTENT, encoding="utf-8")
    with session_scope(engine) as session:
        import_agent_payroll_csv(session, csv_path)
        upsert_agent_payroll_manual(session, manual_path)

    client = _client()
    _login(client)

    response = client.get("/admin/payroll")

    assert response.status_code == 200
    assert "Paie agents" in response.text
    assert "Mode d’emploi" in response.text
    assert "Objectif : suivre les absences" in response.text
    assert "Exemple agent" in response.text
    assert "Yazid" in response.text
    assert "Absence non justifiée" in response.text
    assert "N’est pas allé travailler cet après-midi" in response.text
    assert "À vérifier" in response.text
