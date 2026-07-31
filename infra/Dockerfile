FROM python:3.12-slim

RUN pip install --no-cache-dir uv

# Named /code, not /app, to avoid confusion with the app/ Python package copied into it.
WORKDIR /code
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
