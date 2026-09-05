# API + `light` worker pool. No ffmpeg, no Chromium — this image stays small because it
# only ever waits on other people's APIs.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY services/lumina/pyproject.toml services/lumina/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/lumina/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "lumina.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
