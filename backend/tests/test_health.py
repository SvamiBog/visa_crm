from fastapi.testclient import TestClient

from app.main import app


def test_health_ok() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_does_not_name_the_environment() -> None:
    # Anyone may ask it (audit h14.5): which site is a stand is not theirs to learn. The
    # stand's strip reads the page's own mark (app/main.py, frontend/src/App.tsx).
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}


def test_unknown_api_route_is_404() -> None:
    assert TestClient(app).get("/api/nope").status_code == 404
