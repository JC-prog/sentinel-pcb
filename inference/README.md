# Inference service

Internal FastAPI service that runs SentinelChat's ONNX image classifiers. The caller names which
model to run (contract: `model`, `username`, `file`); this service does no model selection of its
own. It runs as a second ECS Fargate task (`infra/production/inference.tf`), reachable only from
the backend over the VPC's private DNS - no ALB, no public route.

## API

| Method | Path        | Body                                          | Returns |
| ------ | ----------- | --------------------------------------------- | ------- |
| GET    | `/health`   | -                                             | `{status, models}` |
| GET    | `/models`   | -                                             | `[{name, version, previous_version, loaded_at, labels, input_size}]` |
| POST   | `/classify` | multipart: `model`, `username`, `file` (image) | `{model, model_version, username, label, index, confidence, scores, request_id}` |
| POST   | `/models/{name}/activate` | `{repo_id, revision}`           | the updated model info (404 unknown model, 422 file doesn't fit the model's labels/input, 502 fetch failed) |
| POST   | `/models/{name}/rollback` | -                               | the updated model info (409 if nothing to roll back to) |
| POST   | `/jobs`     | `{model, client_ref, samples[], base_version?, notes?}` | `202` job (idempotent on `client_ref`) |
| GET    | `/jobs`, `/jobs/{id}` | -                                   | job(s): `{id, client_ref, model, base_version, status, progress, sample_count, result, error, ...}` |
| POST   | `/jobs/{id}/cancel` | -                                       | the job (409 if it already succeeded/failed) |

A model's `version` is `<repo_id>@<revision>` from `models.toml` - pin `revision` to a commit SHA
so it means something. `/classify` reports the version that produced each verdict.

`username` is logged for request tracking, not used for auth (this service isn't
internet-facing). One structured log line per request; `LOG_FORMAT=json` in production.

## Models

`models.toml` is the single source of truth - one `[[models]]` block per classifier, with its
Hugging Face source and its preprocessing (input size, layout, channel order, normalization,
whether to apply softmax). **Every `REPLACE_ME` must be filled in** before the image will build.

`scripts/fetch_models.py` reads that file and downloads each ONNX into `model_store/` at image
build time, so the running container has no dependency on Hugging Face for the *baseline*
versions. A newer version can be hot-swapped without a rebuild via `POST /models/{name}/activate`:
it downloads `repo_id@revision` (set `HF_TOKEN` for private repos - so the container then needs
outbound access to Hugging Face), loads and smoke-tests it, and only then swaps it in. The previous
version stays loaded, so `POST /models/{name}/rollback` is instant. A new version must be a drop-in:
labels, input size and preprocessing are inherited from the version it replaces.

## Retraining jobs

`POST /jobs` queues a retraining request; one worker runs jobs one at a time. The trainer is behind
a `Trainer` protocol (`trainer.py`) and only a **stub** exists: it sleeps for
`STUB_TRAINER_SECONDS`, trains nothing, and reports `result.simulated = true` with the base version
as its artifact. Jobs live in memory - the backend's database is the durable record and reconciles
against `GET /jobs`, treating a job this service no longer knows as lost. Plugging in a real
trainer means a new `Trainer` class and a branch in `get_trainer()`; it will also need a way to get
the flagged images (they live in the backend's upload store) and to publish weights somewhere
`/activate` can fetch them.

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
| `STUB_TRAINER_SECONDS`      | `3.0`          | how long the stub trainer "trains" |
| `JOB_HISTORY_LIMIT`         | `200`          | finished jobs kept in memory |
| `HF_TOKEN`                  | -              | read by `/activate` (and the image build) for private repos |
