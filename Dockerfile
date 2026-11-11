# API image. Needs a built database and a trained model mounted at /app/data and /app/models.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY brace ./brace
ENV PATH="/app/.venv/bin:$PATH" BRACE_DB=/app/data/brace.duckdb BRACE_MODEL=/app/models/brace.joblib
EXPOSE 8000
CMD ["uvicorn", "brace.api:app", "--host", "0.0.0.0", "--port", "8000"]
