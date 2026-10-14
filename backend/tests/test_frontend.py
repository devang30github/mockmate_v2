from fastapi.testclient import TestClient

from app.main import app


def get(path):
    with TestClient(app) as client:
        return client.get(path)


def test_landing_page_served():
    r = get("/")
    assert r.status_code == 200
    assert "MockMate" in r.text


def test_auth_and_dashboard_pages_served():
    assert get("/auth.html").status_code == 200
    assert get("/dashboard.html").status_code == 200


def test_js_module_served_with_javascript_type():
    r = get("/js/api.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_api_routes_not_shadowed_by_static_mount():
    r = get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_unknown_api_path_is_404():
    assert get("/api/v1/does-not-exist").status_code == 404