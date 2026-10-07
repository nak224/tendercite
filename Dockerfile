FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home appuser && mkdir -p /app/data/uploads && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "tendercite.main:app", "--host", "0.0.0.0", "--port", "8000"]
