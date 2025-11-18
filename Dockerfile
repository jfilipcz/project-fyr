# syntax=docker/dockerfile:1.6

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install build dependencies for python packages (needed for e.g. mysqlclient, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY project_fyr /app/project_fyr

RUN pip install --upgrade pip && \
    pip install .

CMD ["python", "-m", "project_fyr.watcher_service"]
