from app.agent_payroll import agent_payroll_input_from_csv_row


def test_payroll_transfer_csv_row_mapping_matches_legacy_columns():
    row = {
        "Date": "2026-06-02",
        "Agent": "Yazid",
        "Type": "Absence non justifiée",
        "Prévenu ?": "Non",
        "Heure début prévue": "",
        "Heure fin prévue": "",
        "Heure réelle début": "",
        "Heure réelle fin": "",
        "Durée impactée (h)": "",
        "Justificatif": "Aucun",
        "Commentaire": "N’est pas allé travailler cet après-midi et n’a pas prévenu. Saisi depuis vocal WhatsApp.",
        "Validé par": "",
        "Impact paie (MAD)": "",
        "Statut": "À vérifier",
    }

    parsed = agent_payroll_input_from_csv_row(
        row,
        source_file="/home/ubuntu/suivi_absences_agents_paie.csv",
        source_row_number=3,
    )

    assert parsed.event_date.isoformat() == "2026-06-02"
    assert parsed.agent_name == "Yazid"
    assert parsed.event_type == "Absence non justifiée"
    assert parsed.warned == "Non"
    assert parsed.justification == "Aucun"
    assert parsed.impacted_hours is None
    assert parsed.payroll_impact_dh is None
    assert parsed.status == "À vérifier"
