from fastapi.testclient import TestClient

from app.main import app


def test_health_is_served_at_both_paths() -> None:
    # /api/health is the one reachable through CloudFront, which only forwards /api/*.
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}
