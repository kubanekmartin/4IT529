# syntax=docker/dockerfile:1
# Jeden image pro všechny služby (web, migrace, agenti); služba v compose vybírá jen příkaz.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Nejdřív jen závislosti, aby změna kódu nestahovala balíčky znovu.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/raw \
    && chown app /data/raw
USER app

CMD ["lihoviny", "--help"]
