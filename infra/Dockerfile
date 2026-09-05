FROM python:3.12-slim

RUN pip install --no-cache-dir uv

# Named /code, not /app, to avoid confusion with the app/ Python package copied into it.
WORKDIR /code
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

# Invoke the venv's own uvicorn directly rather than "uv run uvicorn ..." - uv run re-checks the
# environment against the lockfile (including dev-only packages) on every start, which both
# needs network access to PyPI at container startup and pulls in dev tooling that has no business
# being in a production image.
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
