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

## Observabilité (Langfuse)

Optionnel. Avec `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` dans le `.env`, le bot
trace chaque question (modèle, tokens, latence) et le runner d'éval (`--langfuse`)
pousse le dataset + les traces + les **scores du juge** dans Langfuse. Les détails
internes (IP, domaines homelab) sont **rédactés** avant envoi par défaut
(`LANGFUSE_REDACT=1`) — important si l'instance est en cloud. Sans clés : désactivé.

## Serveur MCP `data-platform`

En plus du bot Discord, le repo fournit un **serveur MCP autonome** (Model Context
Protocol) qui réexpose les outils corpus à **tout client MCP** (Claude Code, Claude
Desktop, IDE…), sans Discord. Code : `src/ic_data_bot/mcp_server.py` (FastMCP,
transport streamable-HTTP) ; lancement : console script `ic-data-bot-mcp`.

**Ce qu'il fournit** (sur le snapshot `data-platform`) :
- **Tools** : `read_file`, `grep`, `lineage`, `list_corpus` ;
- **Resource** : `dataplatform://{chemin}` (lecture d'un fichier du corpus) ;
- **Prompt** : `data_manager_persona` (le persona du bot, à adopter par le client).

`kestra_recent` n'est PAS exposé (il dépend de l'état live alimenté par Discord).

**Sécurité** :
- mode **public** de `ToolBox` : l'overlay confidentiel `_ops/` (détails infra) est
  **refusé** par read_file/grep/lineage ; le serveur clone le repo `data-platform`
  **public, anonymement**, dans un snapshot dédié (jamais d'overlay) ;
- **auth bearer** obligatoire — `MCP_BEARER_TOKEN` + `MCP_BEARER_TOKENS` (un token par
  personne, révocable) → 401 sinon ;
- derrière un reverse-proxy, autoriser le host public via `MCP_ALLOWED_HOSTS`.

**Observabilité** : si les clés Langfuse sont présentes, chaque appel d'outil est tracé
(span `mcp.<outil>`, I/O rédactés) avec l'**identité du porteur** du token
(`MCP_TOKEN_LABELS=token:nom`, sinon `user-<hash>`).

**Lancer** (conteneur dédié, séparé du bot) :

```bash
docker compose -f docker-compose.mcp.yml up -d --build
```

**Connecter Claude Code** :

```bash
claude mcp add --scope user --transport http <nom> \
  https://<votre-hôte>/mcp --header "Authorization: Bearer <token>"
```

Variables d'environnement : voir le bloc « Serveur MCP » de `.env.example`.

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
