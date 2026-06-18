# Quant trading system — container image.
# Bundles the `quant` CLI with all engines + the TimescaleDB driver, so the same
# image runs research, backtests, and the (paper) live/schedule loop.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Project metadata + source. `config/` is a top-level import (not under src/),
# so it must be on PYTHONPATH — hence PYTHONPATH=/app above.
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY portfolios ./portfolios

# Editable install pulls all deps (manylinux wheels — no compiler needed on slim).
# `[timescale]` adds the psycopg driver so the timescale storage backend works.
# If a dependency ever needs to build from source, add build-essential here.
RUN pip install --upgrade pip && pip install -e ".[timescale]"

# Runtime dirs (bind-mounted to the host in docker-compose so output persists).
RUN mkdir -p data logs reports

# Default to a harmless sanity check. Override per task, e.g.:
#   docker compose run --rm quant download SPY --start 2020-01-01
#   docker compose run --rm quant backtest SPY --strategy momentum
ENTRYPOINT ["quant"]
CMD ["info"]
