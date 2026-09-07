FROM python:3.12-slim

# libgomp1: onnxruntime's CPU provider needs it. curl: container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /code
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY scripts ./scripts
COPY models.toml ./models.toml

# Bake the ONNX files into the image so there's no runtime dependency on Hugging Face. Pass a
# token for private repos with:  docker build --secret id=hf_token,src=<file> ...
ENV MODEL_STORE_DIR=/code/model_store
RUN --mount=type=secret,id=hf_token,required=false \
    HF_TOKEN="$( [ -f /run/secrets/hf_token ] && cat /run/secrets/hf_token || true )" \
    .venv/bin/python scripts/fetch_models.py --manifest models.toml --out "$MODEL_STORE_DIR"

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8001/health || exit 1

# uvicorn from the venv directly (not "uv run") - see infra/Dockerfile for why.
CMD [".venv/bin/uvicorn", "inference_service.main:app", \
     "--app-dir", "src", "--host", "0.0.0.0", "--port", "8001"]
