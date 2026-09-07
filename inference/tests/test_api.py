import io

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import TEST_LABELS


def test_health_lists_loaded_models(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["models"] == ["classifier_1"]


def test_models_endpoint_reports_labels(client: TestClient) -> None:
    response = client.get("/models")
    assert response.status_code == 200
    (model,) = response.json()
    assert model["name"] == "classifier_1"
    assert model["labels"] == TEST_LABELS
    assert model["input_size"] == [32, 32]


def test_classify_returns_a_label_from_the_manifest(client: TestClient, png_bytes: bytes) -> None:
    response = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "jane-qa"},
        files={"file": ("board.png", png_bytes, "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "classifier_1"
    assert body["username"] == "jane-qa"
    assert body["label"] in TEST_LABELS
    assert set(body["scores"]) == set(TEST_LABELS)
    assert abs(sum(body["scores"].values()) - 1.0) < 1e-5
    assert body["scores"][body["label"]] == max(body["scores"].values())
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


def test_classify_is_deterministic(client: TestClient, png_bytes: bytes) -> None:
    def once() -> str:
        response = client.post(
            "/classify",
            data={"model": "classifier_1", "username": "u"},
            files={"file": ("b.png", png_bytes, "image/png")},
        )
        return str(response.json()["label"])

    assert once() == once()


def test_classify_unknown_model_is_404(client: TestClient, png_bytes: bytes) -> None:
    response = client.post(
        "/classify",
        data={"model": "nope", "username": "u"},
        files={"file": ("b.png", png_bytes, "image/png")},
    )
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_classify_rejects_non_image(client: TestClient) -> None:
    response = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "u"},
        files={"file": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert response.status_code == 400


def test_classify_rejects_empty_upload(client: TestClient) -> None:
    response = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "u"},
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert response.status_code == 400


def test_classify_requires_username(client: TestClient, png_bytes: bytes) -> None:
    response = client.post(
        "/classify",
        data={"model": "classifier_1"},
        files={"file": ("b.png", png_bytes, "image/png")},
    )
    assert response.status_code == 422


def test_classify_accepts_a_grayscale_image(client: TestClient) -> None:
    buffer = io.BytesIO()
    Image.new("L", (20, 20), 128).save(buffer, format="PNG")
    response = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "u"},
        files={"file": ("gray.png", buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 200
