#!/usr/bin/env bash
set -euo pipefail

# Clone (sparse/partial) si absent, sinon pull. Échoue le démarrage seulement
# si aucun contexte n'est disponible (cf. gitsync.main).
uv run --no-sync python -m ic_data_bot.gitsync

exec uv run --no-sync ic-data-bot
