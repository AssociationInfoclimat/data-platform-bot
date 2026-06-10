FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy

# Couche de dépendances (cache) — projet pas encore copié
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# Code
COPY . .
RUN uv sync --no-dev --frozen \
 && chmod +x entrypoint.sh

# Version déployée (injectée par scripts/deploy.sh ; "dev" en build manuel)
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

# Utilisateur non-root + répertoires de données inscriptibles
RUN useradd -m -u 10001 botuser \
 && mkdir -p /data/site-infoclimat /var/lib/ic-data-bot \
 && chown -R botuser:botuser /app /data /var/lib/ic-data-bot
USER botuser

ENTRYPOINT ["./entrypoint.sh"]
