# Positionnement — ic-data-bot vs outils d'intelligence de code

> Comparaison de notre stack (bot Discord + serveur MCP + index `code_index`) avec trois
> projets open-source de référence. Snapshot du 2026-06-20 ; les capacités évoluent.

Repos comparés :
- **socraticode** — <https://github.com/giancarloerra/socraticode> — serveur MCP d'intelligence
  de code (chunking AST, hybride, graphe d'appels symbol-level, viz).
- **Understand-Anything** — <https://github.com/Egonex-AI/Understand-Anything> — plugin
  multi-plateforme : graphe de connaissance, couches d'architecture, tours guidés, dashboard.
- **januscope** — <https://github.com/giancarloerra/januscope> — proxy de **politique MCP**
  (rate-limit, audit, vault, quarantaine, redaction) — orthogonal au RAG.

> ⚠️ **januscope n'est pas un concurrent sur le RAG / l'intelligence de code.** C'est une
> **couche de sécurité/gouvernance** qui se place DEVANT n'importe quel serveur MCP. Dans les
> tableaux RAG/graphe ci-dessous il est donc « — » partout *par nature* (il ne fait pas ces
> choses), et il n'apparaît vraiment que sur la ligne « durcissement MCP ». On le garde dans la
> comparaison non pour le battre, mais parce qu'il décrit **ce qu'on pourrait mettre devant
> notre propre MCP** (cf. la section dédiée en fin de document).

Notre stack : `data-platform/tools/code_index` (index vectoriel + graphe) exposé par
`ic-data-bot` (outils `search_code`, `search_docs`, `code_impact`, `code_hotspots`, `lineage`,
`grep`, `read_file`, `schema`, `volumetrie`) via le bot Discord **et** le serveur MCP.

## Retrieval / RAG

| Capacité | socraticode | Understand-Anything | januscope | **Nous** |
|---|---|---|---|---|
| Chunking AST (tree-sitter) | ✅ 18+ langs | ✅ | — | ✅ 4 langs (PHP/Py/TS/JS) |
| Hybride vecteur + BM25 + RRF | ✅ Qdrant | partiel | — | ✅ LanceDB |
| Contextual Retrieval (contexte LLM/chunk) | ❌ | ❌ | — | ✅ *(on mène)* |
| Réécriture de requête | ❌ | ❌ | — | ✅ *(on mène)* |
| Rerank autorité/récence (anti-legacy) | ❌ | ❌ | — | ✅ *(on mène)* |
| Hygiène d'index (code ≠ docs, dé-pollué) | partiel | partiel | — | ✅ |
| Corpus gouvernance séparé | ✅ context artifacts | ✅ | — | ✅ `search_docs` |

## Intelligence de code (graphe d'appels)

| Capacité | socraticode | Understand-Anything | januscope | **Nous** |
|---|---|---|---|---|
| Graphe d'appels | ✅ symbol-level | ✅ | — | ✅ |
| Blast-radius (« qu'est-ce qui casse ») | ✅ | ✅ diff impact | — | ✅ `code_impact` |
| Impact au niveau fichier | ✅ | ✅ | — | ✅ |
| Résolution à **confiance** + hiérarchie de classes | ❌ (name/ast-grep) | ❌ | — | ✅ *(on mène)* |
| Tiers **Certain / Probable / Incertain** | ❌ | ❌ | — | ✅ *(unique)* |
| Hubs / centralité PageRank | ❌ | partiel | — | ✅ `code_hotspots` |
| Détection de cycles | ✅ (+ Mermaid) | ✅ | — | ❌ |
| Visualisation interactive | ✅ Cytoscape | ✅ dashboard web | — | ❌ (texte / Discord) |
| Tours / onboarding | partiel | ✅ | — | ❌ |

## Gouvernance, sourcing, sécurité

| Capacité | socraticode | UA | januscope | **Nous** |
|---|---|---|---|---|
| Lineage **data** curé (ODCS / OpenLineage) | — | — | — | ✅ *(unique)* |
| Sourcing par URL exacte + anti-fabrication + whitelist | — | — | — | ✅ *(unique)* |
| Caviardage secrets (regex + LLM) | — | — | ✅ (PII) | ✅ |
| MCP : rate-limit / vault / quarantaine / audit JSONL | — | — | ✅ | ❌ partiel (bearer, `_ops`, Langfuse) |

## Modèle / surface

| | socraticode | UA | januscope | **Nous** |
|---|---|---|---|---|
| Langages couverts | 18+ | multi | — | 4 (PHP/Py/TS/JS) |
| 100 % local / privé | ✅ Ollama | dépend | proxy local | ❌ (API Mistral, clé jamais sortie de la VM) |
| Cache de prompt exploité | n/a | n/a | ✅ (mais Mistral l'ignorait) | ✅ mistral-small + prompt allégé |
| Surface | MCP / IDE | IDE + web | proxy | **Discord + MCP** |

## Lecture stratégique

On a **refermé les manques cœur** face à socraticode / Understand-Anything (graphe d'appels,
blast-radius, impact fichier, hubs), et sur plusieurs axes on est **devant** :

- **Qualité de résolution** : cascade à score de **confiance** + résolution par hiérarchie de
  classes (`$this->`/`self::`/`parent::`) + contrainte same-langue, avec restitution en tiers
  *Certain / Probable / Incertain*. socraticode et UA restent en correspondance de nom brute,
  sans score ni aveu d'ambiguïté.
- **Ancrage anti-hallucination** : permaliens commit-SHA, whitelist d'URLs externes, persona
  qui interdit d'inventer une source — aucun des trois n'a cette discipline.
- **Gouvernance data** : `lineage` curé (ODCS / OpenLineage) + corpus `search_docs` — terrain
  que les trois ne couvrent pas (eux font du *code*, pas de la *donnée gouvernée*).
- **Hygiène d'index** : séparation stricte code / docs et dé-pollution (la gouvernance et les
  fixtures d'éval ne sont plus dans le corpus *code*) → `search_code` rend du **code**, pas de
  la prose. Soin que les trois ne formalisent pas.

### Manques restants (présentation / largeur, pas le fond)

1. **Visualisation interactive + détection de cycles + tours d'onboarding** (socraticode / UA) —
   on est texte/Discord ; un graphe cliquable relèverait d'un dashboard séparé.
2. **Couverture langages** (4 vs 18+) — acceptable : le monolithe est surtout PHP ; NCL/shell/
   SQL passent en repli char-window côté index et hors graphe.
3. **Durcissement MCP** façon januscope (rate-limit, vault, quarantaine, audit structuré) — à
   traiter si on ouvre davantage le serveur MCP public.

### januscope : un complément, pas un concurrent

januscope joue dans une autre cour : c'est un **proxy stdio** qui enveloppe un serveur MCP
pour y ajouter, sans toucher au serveur lui-même : rate-limiting par outil (token-bucket),
audit JSONL par appel (hash SHA-256 des arguments), redaction PII, secrets en vault (Vault /
AWS / 1Password), quarantaine au 1ᵉʳ usage (anti tool-poisoning), et injection/cache de schéma.
Son gain phare « cache de schéma −84 % tokens » **ne s'appliquait pas sur Mistral** (cache
ignoré) — mais désormais qu'on est sur **mistral-small + cache**, cet angle redevient pertinent.

C'est donc l'outil à considérer **si on ouvre davantage notre serveur MCP public** : il
viendrait se placer devant `ic-data-bot-mcp` pour le durcir. Aujourd'hui on couvre une partie
de ce périmètre nativement (bearer auth, exclusion `_ops` en mode public, caviardage secrets,
tracing Langfuse) ; il manque rate-limit, audit structuré par appel, vault et quarantaine —
d'où le « ❌ partiel » sur la ligne correspondante. Hors périmètre tant que le besoin sécurité
ne se concrétise pas.

### À noter

Aucun labo IA (Anthropic, OpenAI, Google, Microsoft) ne publie de « recette » call-graph
équivalente au *Contextual Retrieval*. Anthropic prône même l'inverse pour le code (recherche
**agentique** : grep/glob plutôt qu'un index précalculé). La cascade de résolution à confiance
qu'on utilise vient de l'OSS/académique (scope/stack graphs GitHub, SCIP Sourcegraph, cascade
« Codebase-Memory ») ; le PageRank des hubs s'inspire du *repo-map* d'Aider ; le clustering par
sous-système, de la moitié « analyse » de Microsoft GraphRAG.
