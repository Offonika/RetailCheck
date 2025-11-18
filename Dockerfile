# syntax=docker/dockerfile:1.7-labs
FROM python:3.11-slim AS base

ENV POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml poetry.lock* README.md* docs/README.md* ./
RUN $POETRY_HOME/bin/poetry install --no-root --only main

COPY . .

RUN $POETRY_HOME/bin/poetry install --no-root

EXPOSE 8080

CMD ["python", "-m", "retailcheck"]
