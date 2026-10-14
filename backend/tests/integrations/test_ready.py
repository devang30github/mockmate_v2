from fastapi.testclient import TestClient

from app.main import app


def test_ready_reports_database_ok():
    with TestClient(app) as client:
        r = client.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "database": "ok"}