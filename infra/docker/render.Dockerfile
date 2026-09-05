# `cpu-render` pool: ffmpeg composition + Chromium caption rasterization.
#
# This is the expensive image and the expensive pool — it is why the render workers run on
# dedicated hardware rather than a hyperscaler. Keep it separate from the API image so the
# API never pays for a 400MB browser it does not use.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-noto-core fonts-noto-cjk fonts-noto-unhinted \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/lumina/pyproject.toml services/lumina/uv.lock ./
RUN uv sync --frozen --no-dev --extra render --no-install-project

# Chromium only — the full Playwright browser set is three browsers we never use.
RUN uv run playwright install --with-deps chromium

COPY services/lumina/src ./src
RUN uv sync --frozen --no-dev --extra render

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "lumina.workers.render"]
