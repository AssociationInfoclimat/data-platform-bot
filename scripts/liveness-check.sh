#!/usr/bin/env bash
# Sentinelle de vie du conteneur — à lancer par cron sur l'hôte (ex. */5).
# Alerte un webhook Discord UNIQUEMENT sur changement d'état (anti-spam),
# avec message de rétablissement. Sans DISCORD_ALERT_WEBHOOK dans le .env : no-op.
#
# Limite assumée : si l'hôte lui-même tombe, la sentinelle tombe avec lui
# (une vraie supervision externe devrait vivre sur une autre machine).
set -euo pipefail

CONTAINER="${CONTAINER:-ic-data-bot-bot-1}"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}"
STATE_FILE="${STATE_FILE:-/tmp/ic-data-bot-liveness.state}"

WEBHOOK=$(grep -E '^DISCORD_ALERT_WEBHOOK=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
[ -n "$WEBHOOK" ] || exit 0

status=$(docker inspect "$CONTAINER" \
  --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
  2>/dev/null || echo "absent")

case "$status" in
  running/healthy|running/starting) current="ok" ;;  # starting = déploiement en cours
  *) current="ko:$status" ;;
esac

last=$(cat "$STATE_FILE" 2>/dev/null || echo "ok")
[ "$current" = "$last" ] && exit 0

if [ "$current" = "ok" ]; then
  msg="✅ **ic-data-bot** est rétabli (état : \`$status\`)."
else
  msg="🚨 **ic-data-bot** en difficulté — état : \`$status\`. Voir \`docker compose logs bot\` sur l'hôte."
fi

curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
  -d "{\"content\": \"$msg\"}" "$WEBHOOK" >/dev/null

# État persisté seulement après envoi réussi : un échec d'envoi (réseau,
# 400 transitoire du webhook…) sera retenté au prochain passage du cron.
echo "$current" > "$STATE_FILE"
