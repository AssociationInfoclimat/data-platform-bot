# Dataset d'évaluation — ic-data-bot

Questions calibrées à poser dans Discord après tout changement de modèle, de
prompt, d'outil ou d'index. Vérifier la mécanique dans les logs
(`docker compose logs | grep '"evt"'` → champ `iters`) en plus du contenu.

## Lancer les évals hors Discord

`scripts/run_eval.py` exécute ces questions directement contre l'agent, sans
passer par Discord (reproductible, mesuré : iters/tokens/durée). Il construit un
agent par provider dont la clé est dans le `.env` → **comparaison Mistral vs Haiku
automatique** si les deux clés sont présentes. Backfill Kestra inclus (pour E5).

```bash
# dans le conteneur (snapshot + deps + .env y vivent)
docker compose exec bot uv run python scripts/run_eval.py          # toutes les évals
docker compose exec bot uv run python scripts/run_eval.py E1 E3    # évals choisies
docker compose exec bot uv run python scripts/run_eval.py -q "question libre"
```

Modèles : le provider actif (`PROVIDER`) utilise `MODEL` ; l'autre, s'il a une
clé, utilise `ANTHROPIC_MODEL` / `MISTRAL_MODEL` (défauts claude-haiku-4-5 /
mistral-small-latest).

---

## E1 — Récupération profonde (piège absent de l'index)

> @bot-data-ic dans la table foudre, que contient exactement la colonne dh_usec ? Il y a un piège ?

L'info n'existe que dans le contrat complet (`description.usage` + description
de colonne) — pas dans l'index. Réussir = avoir fait un `read_file`.

**Critères :**
1. dh_usec contient des **millisecondes (0-999)** malgré le nom (bonus : `round(fraction*1000)`, `recup_blitzortung.php:81`)
2. Source citée : `contracts/foudre.odcs.yaml`
3. Bonus : déduplication par la colonne `key` (md5), pas par (dh, lat, lon)

**Échecs typiques :** « microsecondes » (hallucination depuis le nom) ;
« information absente du snapshot » (n'a pas lu le fichier).

**Baseline 2026-06-09 :** ✅ 5/5 (Haiku 4.5).

---

## E2 — Témoin index (zéro outil attendu)

> @bot-data-ic quels contrats ODCS existent et lesquels sont en draft ?

Tout est dans l'index du préfixe : réponse rapide, sans tableau Markdown,
< 1500 caractères.

**Critères :** les 6 contrats, statuts exacts (3 active / 3 draft),
liste à puces. `iters` attendu : 1 (tolérance 2).

**Baseline 2026-06-10 :** ✅ (format puces validé après ajout du bloc FORMAT).

---

## E3 — Analyse d'impact (outil lineage + croisement contrat)

> @bot-data-ic on envisage de décommissionner la table foudre (V5) pendant la migration : qu'est-ce qui casse en aval, et qui écrit encore dedans ?

Exige la jointure des registres (outil `lineage`) PUIS la lecture du contrat
pour nuancer. `iters` attendu : **3** (lineage → read_file contrat → synthèse).

**Critères (sur 6) :**
1. `notif-foudre` nommé + impact notifications push (`appli_notifications`)
2. Chaîne cartes : `extract_foudre.php` / `cron.foudre` / SHP MapServer
3. Writers `recup_blitzortung.php` (+ `_ws`) + doublon cron/Kestra signalé
4. **Croisement contrat** : flux Blitzortung mort ~2023, writer « actif » douteux — réconcilie registre et contrat au lieu de trancher
5. Cohérence interne (pas de « cassé » puis « rien à faire »)
6. ≥ 2 registres + le contrat cités en sources

**Historique :**
| Essai | Réglage | Score | iters |
|---|---|---|---|
| 2026-06-10 #1 | baseline | 4/6 | 2 |
| 2026-06-10 #2 | consigne dans la description d'outil | 4,5/6 | 2 |
| 2026-06-10 #3 | rappel in-band dans le résultat de lineage | **6/6** | 3 |

**Leçon :** avec Haiku, une consigne de synthèse se place dans le **résultat**
de l'outil (lue au moment de la synthèse), pas dans sa description.

---

## E4 — Jointure overlay ops privé + sources publiques

> @bot-data-ic sur quel hôte tourne TimescaleDB et c'est quoi son IP ?

L'IP n'existe que dans l'overlay privé `_ops/ops-mapping.yaml` (absent du
repo public) ; les détails techniques (versions, volumétrie) sont dans les
audits publics. Réussir = joindre les deux.

**Critères :**
1. Hôte logique (ct-timescale) ET l'IP interne — celle-ci ne peut venir
   que de `_ops/` (la vérifier contre le mapping, ne pas la documenter ici)
2. Enrichissement depuis les sources publiques (versions PG/Timescale,
   volumétrie, hypertables)
3. Sources citées, dont `_ops/ops-mapping.yaml`

**Échec typique :** « l'IP ne figure pas dans le snapshot » → l'overlay
n'est pas installé (vérifier `_ops/` dans le snapshot) ou pas joint.

**Baseline 2026-06-11 :** ✅ 3/3 (jointure _ops + registre public + audits).

---

## E5 — Fraîcheur temps réel (événements Kestra via Discord)

> @bot-data-ic la climato est à jour ?

L'info n'est PAS dans le snapshot : elle vient du cache d'événements Kestra
(notifications relayées dans les canaux Discord d'infra, fenêtre 48 h).
Réussir = `kestra_recent` + jointure avec la règle `quality: freshness`
du contrat.

**Critères :**
1. Dernier succès du flow de rafraîchissement cité avec son âge relatif
2. Verdict rapporté à la règle de fraîcheur du contrat (ex. ≤ 6 min)
3. `iters` attendu : 3 (kestra_recent → read_file contrat → synthèse)

**Variante incidents :**
> @bot-data-ic il y a eu des échecs de flows récemment ?

Attendu : les échecs réels du cache cités nommément avec leurs âges ;
si aucun échec ne matche : nuance d'incertitude (« aucun échec notifié
≠ garantie » — les notifications peuvent être en panne).

**Baseline 2026-06-11 :** ✅ validée en prod (succès + variante incidents).
