from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin
from app.cash_ledger import CashLedgerEntryInput, create_cash_ledger_entry
from app.config import settings
from app.db import init_db, make_engine, session_scope
from app.models import CashLedgerAuditEventRow, CashLedgerEntryRow


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post("/admin", data={"password": settings.admin_password}, follow_redirects=False)
    assert response.status_code == 303


def test_admin_cash_page_allows_category_edit_and_marks_review_resolved(monkeypatch, tmp_path):
    db_path = tmp_path / "cash-admin.sqlite3"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    init_db(engine)
    monkeypatch.setattr(admin, "_configured_engine", lambda: engine)
    monkeypatch.setattr(settings, "cash_ledger_owner_phone", "212688636993")

    with session_scope(engine) as session:
        entry = create_cash_ledger_entry(
            session,
            CashLedgerEntryInput(
                owner_phone="212688636993",
                created_by_phone="212688636993",
                direction="cash_out",
                amount_minor=30_000,
                transaction_type="cash_payment",
                category="uncategorized",
                counterparty="Hamza",
                raw_message_text="paid Hamza 300",
            ),
        )
        entry_id = entry.id

    client = _client()
    _login(client)

    page = client.get("/admin/cash")
    assert page.status_code == 200
    assert f'name="entry_id" value="{entry_id}"' in page.text
    assert 'name="category"' in page.text
    assert 'value="staff_labor"' in page.text

    response = client.post(
        "/admin/cash/category",
        data={"entry_id": str(entry_id), "category": "staff_labor"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/cash?lang=fr&updated=1"

    with session_scope(engine) as session:
        updated = session.get(CashLedgerEntryRow, entry_id)
        assert updated is not None
        assert updated.category == "staff_labor"
        assert updated.status == "recorded"
        assert updated.review_reason == ""
        events = session.query(CashLedgerAuditEventRow).order_by(CashLedgerAuditEventRow.id).all()
        assert [event.event_type for event in events] == ["created", "reviewed"]
        assert events[-1].reason == "admin category update"
