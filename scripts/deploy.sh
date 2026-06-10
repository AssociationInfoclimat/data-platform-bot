#!/usr/bin/env bash
# Déploiement sur la VM : git pull + rebuild avec le SHA injecté (cf. !version).
# Usage (sur la VM, dans ~/ic-data-bot) : ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
GIT_SHA=$(git rev-parse --short HEAD)
export GIT_SHA

docker compose up -d --build
echo "--- déployé : ${GIT_SHA} ---"
docker compose ps --format '{{.Names}} {{.Status}}'
