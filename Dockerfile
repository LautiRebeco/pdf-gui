# Mismo intérprete, mismo lock y mismos comandos que CI (factor X).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --all-groups --no-install-project
COPY . .

CMD ["uv", "run", "pytest", "-q"]
