from fastapi.testclient import TestClient

from app.main import app


def test_health_ok() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_api_route_is_404() -> None:
    assert TestClient(app).get("/api/nope").status_code == 404
