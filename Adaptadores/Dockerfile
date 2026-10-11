FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "pydantic>=2.6,<3" \
    "temporalio>=1.5,<2"

COPY ["Adaptadores/ad_linux/", "/app/ad_linux/"]
COPY ["Slice Manager/contracts/contracts/", "/app/contracts/"]

ENTRYPOINT ["python", "-m", "ad_linux"]
