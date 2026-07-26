FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY README.md /app/README.md
COPY backend/pyproject.toml backend/uv.lock /app/backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-dev --extra data --extra memory

COPY backend/src /app/backend/src
COPY migrations /app/migrations
COPY knowledge /app/knowledge
COPY evals /app/evals
COPY data_generator /app/data_generator

ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONPATH="/app"

EXPOSE 8000
CMD ["uvicorn", "commerce_trace.api:app", "--host", "0.0.0.0", "--port", "8000"]
