# syntax=docker/dockerfile:1
# Build Slice Manager (necesita contracts/).
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
COPY slice_manager/pyproject.toml ./slice_manager/pyproject.toml
RUN uv pip install -r slice_manager/pyproject.toml

# 3) Código
COPY slice_manager/ ./slice_manager/
COPY capabilities/ ./capabilities/

RUN useradd --system --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "slice_manager.app.main:app", "--host", "0.0.0.0", "--port", "8000"]