# Dataset d'évaluation — ic-data-bot

Questions calibrées à poser dans Discord après tout changement de modèle, de
prompt, d'outil ou d'index. Vérifier la mécanique dans les logs
(`docker compose logs | grep '"evt"'` → champ `iters`) en plus du contenu.

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
