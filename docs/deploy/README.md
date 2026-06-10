# Déploiement — ic-data-bot (VM dédiée, docker compose)

## Topologie
Bot dans une VM/hôte quelconque, docker compose. **Sortant uniquement** : Discord (Gateway),
Anthropic (API), GitLab (clone/pull HTTPS). Aucune exposition réseau requise : le healthcheck
Docker interroge `/healthz` à l'intérieur du conteneur. Publier le port 8080 (`ports:` dans le
compose) est optionnel, uniquement pour un monitoring externe.

## Prérequis
- Docker + docker compose sur la VM.
- Un **deploy token GitLab** (lecture seule, scope `read_repository`) sur `site-infoclimat`.
- Une **clé Anthropic dédiée** avec limite de dépense mensuelle (hard cap facturation).
- Le **bot Discord** (token) avec l'intent « Message Content » activé, invité dans le canal dev.

## Mise en service
1. `cp .env.example .env` et renseigner : `DISCORD_BOT_TOKEN`, `ALLOWED_CHANNEL_ID`,
   `ANTHROPIC_API_KEY`, `GITLAB_DEPLOY_USER`, `GITLAB_DEPLOY_TOKEN`, et vérifier `REPO_URL`.
2. `docker compose build`
3. `docker compose up -d`
4. Vérifier : `docker compose ps` → `healthy` (le healthcheck interne couvre `/healthz` ;
   `/readyz` passe à 200 une fois le bot connecté à Discord et le noyau chargé).

## Rafraîchissement du contexte
Automatique : `git pull` toutes les `REFRESH_INTERVAL_SECONDS` (défaut 1 h), puis reconstruction du
noyau en mémoire. Forcer : `docker compose restart bot`.

## Exploitation
- Logs : `docker compose logs -f bot` (rotation json-file 10m×5).
- État budget quotidien persistant dans le volume `state` (`/var/lib/ic-data-bot`).
- Le clone vit dans le volume `clone` (`/data/site-infoclimat`, sparse `data-platform/`).
- Le token GitLab n'est pas persisté dans `.git/config` (origin nettoyé après clone).

## Sécurité
- Conteneur non-root (uid 10001). Aucun port publié : le bot n'est pas joignable depuis le réseau.
- Secrets dans `.env` (gitignored) ; option durcissement : docker secrets.
