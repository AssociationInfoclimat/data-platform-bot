# ic-data-bot

Bot Discord « manager de la donnée » d'Infoclimat. Répond en @mention, dans un canal
dev privé, aux questions sur la plateforme data (catalogue, contrats ODCS, lineage,
inventaire, migration TimescaleDB), ancré sur un snapshot figé de `data-platform/`.

## Mise en route (local)

1. `cp .env.example .env` puis renseigner `DISCORD_BOT_TOKEN`, `ANTHROPIC_API_KEY`,
   `ALLOWED_CHANNEL_ID`.
2. `make sync-snapshot` — copie `../site-infoclimat/data-platform/` vers `./snapshot/`.
3. `uv sync`
4. `make run`

Resync du contexte quand la branche `feat/data-platform-bootstrap` évolue : `make sync-snapshot`.

## Tests

`make test`

## Protections de facturation

- Clé Anthropic **dédiée** avec limite de dépense mensuelle (console Anthropic) = hard cap.
- In-bot : canal unique, rate-limit/utilisateur, budget quotidien de tokens, `max_tokens` borné,
  lectures d'outil plafonnées.

## Fournisseur LLM

Deux fournisseurs via `PROVIDER` dans le `.env` :
- `anthropic` (défaut) — `ANTHROPIC_API_KEY` + `MODEL=claude-haiku-4-5` ;
- `mistral` — `MISTRAL_API_KEY` + `MODEL=mistral-small-latest`.

L'adaptateur vit dans `src/ic_data_bot/{claude,mistral}.py` (même interface) ; le reste
du bot est commun.

**Raisonnement ciblé (Mistral)** : `MISTRAL_REASONING_MODEL` (défaut `magistral-small-latest`) est utilisé par la commande `!deep <question>` (analyse approfondie à la demande) et par l'expliqueur d'incident. Magistral raisonne nativement — 5× le prix de small mais bien meilleur sur l'impact/lineage complexe ; réservé donc à ces deux usages, le Q&R normal restant sur le modèle rapide. Note : Mistral n'a pas de prompt caching, le préfixe system est
refacturé à chaque message — `DAILY_TOKEN_BUDGET` se vide plus vite qu'avec Haiku+cache.

## Déploiement

Docker compose sur n'importe quel hôte (prérequis et mise en service :
`docs/deploy/README.md`). Secrets dans `.env` (jamais commité, cf. `.env.example`).

```bash
# sur l'hôte, dans le checkout du repo :
./scripts/deploy.sh   # git pull + rebuild, SHA visible via !version
```

## Évaluation

Après tout changement de prompt/outil/modèle : dérouler `docs/evals/questions.md`
et vérifier le champ `iters` dans les logs JSON (`docker compose logs | grep '"evt"'`).
