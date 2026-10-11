FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY ["Network Manager/requirements.txt", "/app/requirements.txt"]
RUN pip install --no-cache-dir -r requirements.txt

COPY ["Network Manager/app/", "/app/app/"]
COPY ["Slice Manager/contracts/contracts/", "/app/contracts/"]
COPY ["Network Manager/temporal_worker.py", "/app/temporal_worker.py"]

ENTRYPOINT ["python", "temporal_worker.py"]