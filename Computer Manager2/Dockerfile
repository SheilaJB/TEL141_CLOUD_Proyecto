FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY ["Computer Manager2/requirements.txt", "/app/requirements.txt"]
RUN pip install --no-cache-dir -r requirements.txt

COPY ["Computer Manager2/", "/app/"]
COPY ["Slice Manager/contracts/contracts/", "/app/contracts/"]

ENTRYPOINT ["python", "worker.py"]
