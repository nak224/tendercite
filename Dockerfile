# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TENDERCITE_DATA_DIR=/app/data \
    HF_HOME=/app/data/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=secret,id=ca_bundle \
    if [ -f /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle PIP_CERT=/run/secrets/ca_bundle; \
    fi; \
    pip install --no-cache-dir uv==0.12.19 && \
    uv pip install --system --no-cache --torch-backend cpu --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY frontend ./frontend
RUN --mount=type=secret,id=ca_bundle \
    if [ -f /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle; \
    fi; \
    uv pip install --system --no-cache --no-deps . && \
    useradd --uid 10001 --create-home appuser && \
    mkdir -p /app/data/uploads && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000 8501
CMD ["uvicorn", "tendercite.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
