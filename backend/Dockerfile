FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ENABLE_BACKGROUND_JOBS=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt ./requirements.txt

RUN python -m pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install --with-deps chromium

# All shared dependencies are pinned above; this package has its own index.
RUN python -m pip install --no-cache-dir --no-deps \
    --index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ \
    emergentintegrations==0.2.2

COPY . .

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
