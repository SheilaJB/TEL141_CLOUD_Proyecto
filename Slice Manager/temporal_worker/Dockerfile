# syntax=docker/dockerfile:1
# Build Temporal Worker (necesita contracts/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_SYSTEM_PYTHON=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 1) Paquete compartido
COPY contracts/ ./contracts/
RUN uv pip install ./contracts

# 2) Dependencias del servicio (solo [project.dependencies]; capa cacheable)
COPY temporal_worker/pyproject.toml ./temporal_worker/pyproject.toml
RUN uv pip install -r temporal_worker/pyproject.toml

# 3) Código
COPY temporal_worker/ ./temporal_worker/

RUN useradd --system --uid 10001 appuser && chown -R appuser /app
USER appuser

# (el worker no expone puertos)
CMD ["python", "-m", "temporal_worker.worker.main"]