# Airflow plus a separate venv with BRACE's locked dependencies, so dbt and
# scikit-learn don't have to share packages with Airflow itself.
FROM apache/airflow:3.3.2-python3.13
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv
COPY pyproject.toml uv.lock /tmp/brace/
RUN cd /tmp/brace && UV_PROJECT_ENVIRONMENT=/home/airflow/brace-venv UV_PYTHON_DOWNLOADS=never \
    uv sync --frozen --no-dev --no-install-project
