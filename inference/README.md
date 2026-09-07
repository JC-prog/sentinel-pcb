# Inference service

Internal FastAPI service that runs SentinelChat's ONNX image classifiers. The caller names which
model to run (contract: `model`, `username`, `file`); this service does no model selection of its
own. It runs as a second ECS Fargate task (`infra/production/inference.tf`), reachable only from
the backend over the VPC's private DNS - no ALB, no public route.

## API

| Method | Path        | Body                                          | Returns |
| ------ | ----------- | --------------------------------------------- | ------- |
| GET    | `/health`   | -                                             | `{status, models}` |
| GET    | `/models`   | -                                             | `[{name, labels, input_size}]` |
| POST   | `/classify` | multipart: `model`, `username`, `file` (image) | `{model, username, label, index, confidence, scores, request_id}` |

`username` is logged for request tracking, not used for auth (this service isn't
internet-facing). One structured log line per request; `LOG_FORMAT=json` in production.

## Models

`models.toml` is the single source of truth - one `[[models]]` block per classifier, with its
Hugging Face source and its preprocessing (input size, layout, channel order, normalization,
whether to apply softmax). **Every `REPLACE_ME` must be filled in** before the image will build.

`scripts/fetch_models.py` reads that file and downloads each ONNX into `model_store/` at image
build time, so the running container has no dependency on Hugging Face. Retraining a model means
rebuilding this image.

## Local development

```bash
cd inference
uv sync
uv run python scripts/fetch_models.py --out model_store   # needs models.toml filled in
uv run uvicorn inference_service.main:app --app-dir src --port 8001 --reload
```

To bring the API up without all four models present, set `REQUIRE_MODELS_ON_STARTUP=false`.

## Checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q
```

The test suite builds a real (tiny) ONNX model on the fly, so it exercises an actual onnxruntime
session without needing any downloaded weights.

## Configuration

Environment variables (see `src/inference_service/settings.py`):

| Var                         | Default        | Notes |
| --------------------------- | -------------- | ----- |
| `LOG_FORMAT`                | `console`      | `json` in production |
| `LOG_LEVEL`                 | `INFO`         | |
| `MANIFEST_PATH`             | `models.toml`  | |
| `MODEL_STORE_DIR`           | `model_store`  | where `fetch_models.py` wrote the ONNX files |
| `REQUIRE_MODELS_ON_STARTUP` | `true`         | fail fast if a manifest model has no file |
| `MAX_UPLOAD_BYTES`          | `10485760`     | request image ceiling |
