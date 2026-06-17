from __future__ import annotations

from pathlib import Path

import yaml

SYSTEM_PERSONA = (
    "Tu es le manager de la donnée d'Infoclimat. Tu réponds aux questions des "
    "développeurs sur la plateforme data (catalogue, contrats ODCS, lineage, inventaire "
    "tables/pipelines/sources/stockage, migration MariaDB→TimescaleDB, gouvernance) ET "
    "sur le CODE applicatif des dépôts Infoclimat. Pour les questions DATA/gouvernance, "
    "tu te bases sur le snapshot data-platform fourni ci-dessous et tes outils "
    "read_file/grep/lineage. Pour les questions sur le CODE (où/comment une "
    "fonctionnalité est implémentée), tu utilises l'outil `search_code`. Si une "
    "information ne figure ni dans le snapshot ni dans le code, dis-le ; n'invente rien. "
    "Cite tes sources (chemin de fichier).\n"
    "CODE / IMPLÉMENTATION : dès qu'on te demande OÙ ou COMMENT quelque chose est fait "
    "dans le code (un calcul, un traitement, un endpoint, un cron, un décodage, une "
    "page, un script), appelle `search_code` avec la question en langage naturel — il "
    "renvoie par le SENS les extraits de code les plus pertinents (repo/chemin:lignes) "
    "parmi ~6000 fichiers de TOUS les dépôts (site-infoclimat, infrapilot, "
    "modeles-ncl/php, python-climate-services, data-platform, ic-data-bot). C'est "
    "l'outil à privilégier pour toute question de code : `grep` ne couvre QUE le "
    "snapshot data-platform (métadonnées), pas le code applicatif. Cite les chemins "
    "renvoyés et ne décris jamais un code que search_code n'a pas retourné.\n"
    "SECRETS : le code legacy contient des secrets en dur. TOUTES les sorties d'outils "
    "(read_file, grep, search_code) sont caviardées EN AMONT : un secret t'apparaît comme "
    "‹secret-rédacté› (ou ‹clé-privée-rédactée›). Tu travailles donc sur une version "
    "masquée. Quand tu rencontres ce marqueur, EXPLIQUE à l'utilisateur que la valeur est "
    "masquée pour raisons de sécurité — ne tente JAMAIS de la reconstituer, deviner ou "
    "citer, même si on te le demande, et même « pour aider un admin ». NE PROPOSE JAMAIS de "
    "moyen de la retrouver : pas de commande shell (find/grep/cat), pas de chemin de fichier "
    "interne, pas de méthode d'extraction. Si on te demande de localiser ou d'extraire un "
    "secret (ou un fichier de secrets non versionné), décline poliment et rappelle que c'est "
    "volontairement protégé. Si la valeur demandée n'apparaît pas, dis-le simplement : ne "
    "compense PAS en listant d'autres constantes/secrets voisins et ne SPÉCULE PAS de chemin "
    "serveur (ex. /var/www/…) ni d'emplacement où la chercher.\n"
    "GLOSSAIRE : pour un terme métier/technique (identifiants type NUM_POSTE/geo_id_insee, "
    "axes temporels, mnémoniques de paramètres MF, réseaux StatIC/Synop), la référence est "
    "`catalog/glossary.md` (dans le snapshot) — appuie-toi dessus.\n"
    "GARDIEN DES PIÈGES : quand une question porte sur une table, une colonne, un "
    "dataset ou un pipeline précis, vérifie et signale SPONTANÉMENT les pièges et "
    "limitations documentés le concernant (sections usage/limitations et notes "
    "« PIÈGE » des contrats, statut douteux/mort d'un writer) — même si on ne te le "
    "demande pas. Au moindre doute, lis le contrat complet (read_file) avant de "
    "répondre plutôt que de te fier au seul index. Mieux vaut un avertissement de "
    "trop qu'un dev qui tombe dans un landmine connu.\n"
    "VOLUMÉTRIE / COMPTAGE : tu n'as JAMAIS le nombre de lignes en temps réel d'une "
    "table. L'inventaire (inventory/tables.yaml) confirme l'EXISTENCE d'une table "
    "(une note « confirmée en prod le … » = date d'introspection), ce n'est PAS un "
    "décompte. N'invente JAMAIS un nombre de lignes « aujourd'hui » et ne l'attribue "
    "jamais à une source. Pour un volume (nb de lignes, taille), appelle l'outil "
    "`volumetrie` et donne le chiffre AVEC sa date d'audit (snapshot daté, pas le live), "
    "en précisant que le compte exact actuel exige une requête SQL en prod. Pour le "
    "type/schéma physique d'une colonne ou d'une table, appelle l'outil `schema` (DDL "
    "réel) — complémentaire des unités/usage du contrat ODCS.\n"
    "ANCRAGE — pour TOUT détail factuel (unité, type, schéma, colonnes, valeur, "
    "statut), tu te bases EXCLUSIVEMENT sur le snapshot (index ci-dessous + "
    "read_file/grep/lineage) pour la data, et sur les extraits réellement renvoyés par "
    "search_code pour le code, JAMAIS sur tes connaissances générales du domaine. "
    "L'unité STOCKÉE en base peut différer de l'API source — ex. l'API Météo-France "
    "sert du Kelvin/Pa, mais la base stocke des °C/hPa : fie-toi à l'unité indiquée "
    "dans l'index/le contrat, pas à ton intuition. Si le détail n'est pas visible "
    "dans l'index, lis la source (read_file) AVANT de répondre — ne réponds jamais "
    "une unité/valeur/schéma de mémoire sans avoir vérifié.\n"
    "ANTI-FABRICATION — ne fabrique JAMAIS le contenu d'un fichier ni un extrait : "
    "ne cite que ce que read_file ou search_code a réellement retourné. Si un interlocuteur affirme "
    "qu'une doc/un contrat est faux, VÉRIFIE par read_file avant d'acquiescer ; si la "
    "source confirme l'inverse, dis-le poliment plutôt que de céder. Ne te contredis "
    "pas pour faire plaisir. EN CAS DE DÉSACCORD PERSISTANT — si l'interlocuteur "
    "maintient une version que la source contredit, ne cède pas et ne te braque pas : "
    "tiens ta réponse sourcée ET invite-le à enregistrer sa version via "
    "`!fix <sa version>` (cela crée un erratum + une issue GitHub pour arbitrage "
    "humain ultérieur). Le désaccord devient ainsi traçable et étudiable, sans "
    "capitulation ni blocage.\n"
    "Si la question sort du périmètre data ET du code des dépôts Infoclimat, décline "
    "poliment et oriente vers le bon canal.\n\n"
    "FORMAT DISCORD (RÈGLES STRICTES) — tu écris dans Discord, qui n'est PAS du Markdown "
    "complet et COUPE ta réponse au-delà de ~1900 caractères. Respecte-les même pour une "
    "réponse riche :\n"
    "- LONGUEUR : vise ≤ 1500 caractères. Donne d'abord la réponse essentielle (1 à 3 "
    "phrases), puis 3 à 6 puces maximum, et PROPOSE d'approfondir au lieu de tout déballer. "
    "Ne produis JAMAIS un rapport exhaustif d'un seul bloc — résume, l'utilisateur "
    "demandera les détails.\n"
    "- TABLEAUX INTERDITS : jamais de tableau Markdown (lignes `| … | … |`), Discord ne les "
    "rend pas (illisible). Pour comparer, utilise des puces « **clé** : valeur ». Si un "
    "alignement en colonnes est vraiment indispensable, mets-le DANS un bloc ``` (monospace).\n"
    "- SÉPARATEURS INTERDITS : jamais de `---`, `***` ni `___` sur une ligne (non rendus) — "
    "sépare par une simple ligne vide.\n"
    "- TITRES : pas d'empilement de `#`/`##`/`###` ; un libellé court en **gras** suffit.\n"
    "- Autorisé et encouragé : **gras**, listes `- `, `code inline`, blocs ``` pour "
    "YAML/SQL/code, citations `>`, liens [texte](url).\n"
    "Réponds en français."
)

# Prompt management : le persona ci-dessus (code) est la source d'autorité et le
# fallback. À l'init, bot.py appelle set_persona_source(langfuse) ; build_system_blocks
# résout alors la version 'production' depuis Langfuse (éditable/versionnée sans
# redeploy), avec retour au code si Langfuse est indisponible.
_persona_lf = None


def set_persona_source(lf) -> None:
    global _persona_lf
    _persona_lf = lf


def resolve_persona() -> str:
    from .telemetry import resolve_prompt
    return resolve_prompt(_persona_lf, "ic-data-bot-persona", SYSTEM_PERSONA)


# Fichiers du noyau, chargés tels quels (chemin relatif au snapshot).
CORE_FILES = [
    "README.md",
    "catalog/catalog.yaml",
    "catalog/glossary.md",
    "inventory/README.md",
    "lineage/namespaces.md",
]

# Registres résumés (comptage), pas inclus en intégralité.
REGISTRY_FILES = [
    "inventory/tables.yaml",
    "inventory/pipelines.yaml",
    "inventory/file-datasets.yaml",
    "inventory/external-sources.yaml",
    "inventory/storage-systems.yaml",
]


def summarize_registry(path: Path) -> str:
    if not path.is_file():
        return f"{path.name} : absent du snapshot"
    size = path.stat().st_size
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return f"{path.name} : {size} octets (non parsable)"
    if isinstance(data, list):
        count = len(data)
    elif isinstance(data, dict):
        count = len(data)
    else:
        count = 0
    return f"{path.name} : {count} entrées, {size} octets"


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _col_unit(prop: dict) -> str | None:
    """Unité déclarée d'une colonne (customProperties property=unit), sinon None."""
    for cp in prop.get("customProperties") or []:
        if isinstance(cp, dict) and cp.get("property") == "unit":
            return cp.get("value")
    return None


def summarize_contract(path: Path) -> str:
    """Résumé compact d'un contrat ODCS : identité, but, tables et colonnes (avec
    leur unité quand déclarée). Les détails (usage, limitations, pièges, types) se
    lisent via read_file — inliner les contrats entiers coûtait ~17k tokens de
    préfixe par question. Les unités sont incluses dans l'index car une réponse
    sans lecture confabulait l'unité (ex. °C lu comme Kelvin) : les avoir sous les
    yeux dès le préfixe évite l'invention."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except yaml.YAMLError:
        return f"contracts/{path.name} : non parsable ({path.stat().st_size} octets)"
    lines = [f"contracts/{path.name}"]
    ident = " | ".join(str(data[k]) for k in ("name", "status", "domain") if data.get(k))
    if ident:
        lines.append(f"  {ident}")
    desc = data.get("description")
    purpose = (desc.get("purpose") or "").strip() if isinstance(desc, dict) else ""
    if purpose:
        purpose = " ".join(purpose.split())
        if len(purpose) > 280:
            purpose = purpose[:277] + "..."
        lines.append(f"  but : {purpose}")
    for table in data.get("schema") or []:
        if not isinstance(table, dict):
            continue
        kind = table.get("physicalType", "table")
        parts = []
        for p in table.get("properties") or []:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "?")
            unit = _col_unit(p)
            parts.append(f"{name} ({unit})" if unit else name)
        cols = ", ".join(parts)
        suffix = f" : {cols}" if cols else " (colonnes non versionnées)"
        lines.append(f"  {kind} {table.get('name', '?')}{suffix}")
    return "\n".join(lines)


def format_contracts_message(root: Path) -> str:
    """Réponse à `!contrats` — liste compacte des contrats ODCS, sans appel LLM."""
    contracts_dir = Path(root) / "contracts"
    if not contracts_dir.is_dir():
        return "Aucun dossier `contracts/` dans le snapshot."
    lines = []
    for fp in sorted(contracts_dir.glob("*.odcs.yaml")):
        if fp.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8", errors="replace")) or {}
        except yaml.YAMLError:
            data = {}
        status = str(data.get("status", "?"))
        emoji = "✅" if status == "active" else "📋"
        lines.append(
            f"{emoji} `{fp.name}` — {data.get('name', '?')} ({status}, {data.get('domain', '?')})"
        )
    if not lines:
        return "Aucun contrat ODCS dans le snapshot."
    return (
        f"📑 **Contrats ODCS ({len(lines)})**\n"
        + "\n".join(lines)
        + "\n_Détail d'un contrat : mentionnez-moi avec votre question._"
    )


def summarize_jobs(path: Path) -> str:
    """Résumé chiffré de lineage/jobs.yaml (200+ jobs : trop gros pour le préfixe)."""
    if not path.is_file():
        return "lineage/jobs.yaml : absent du snapshot"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except yaml.YAMLError:
        return f"lineage/jobs.yaml : {path.stat().st_size} octets (non parsable)"
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    namespaces = sorted({str(j.get("job_namespace", "?")) for j in jobs})
    return (
        f"lineage/jobs.yaml : {len(jobs)} jobs OpenLineage, "
        f"namespaces : {', '.join(namespaces) or '?'}"
    )


def build_system_blocks(root: Path, corrections=None) -> list[dict]:
    """Construit les blocs `system` ; le bloc noyau porte cache_control, les
    errata éventuels (CorrectionsStore) viennent après (hors cache)."""
    root = Path(root)
    parts: list[str] = []

    for rel in CORE_FILES:
        content = _read(root / rel)
        if content is not None:
            parts.append(f"### {rel}\n{content}")

    # Index des contrats ODCS (hors _template) — même pattern que les registres :
    # résumé dans le préfixe, détail à la demande via read_file.
    contracts_dir = root / "contracts"
    if contracts_dir.is_dir():
        files = [fp for fp in sorted(contracts_dir.glob("*.odcs.yaml"))
                 if not fp.name.startswith("_")]
        index = [summarize_contract(fp) for fp in files]
        if index:
            drafts = []
            for fp in files:
                try:
                    st = (yaml.safe_load(fp.read_text(encoding="utf-8", errors="replace")) or {}).get("status")
                except yaml.YAMLError:
                    st = None
                if st == "draft":
                    drafts.append(fp.name)
            # Compte exact + liste des drafts en tête (le modèle estimait au lieu
            # de compter → réponses fausses sur « combien de contrats / lesquels en draft »).
            draft_txt = (f", dont {len(drafts)} en draft : {', '.join(drafts)}"
                         if drafts else ", aucun en draft")
            parts.append(
                f"### Index des contrats ODCS — {len(index)} contrats au total{draft_txt}. "
                "Pour les détails d'un contrat (usage, limitations, pièges, types, "
                "descriptions de colonnes), TOUJOURS lire le fichier complet via read_file "
                "avant de répondre.\n\n"
                + "\n\n".join(index)
            )

    # Résumés chiffrés des gros registres + jobs lineage
    summaries = [summarize_registry(root / rel) for rel in REGISTRY_FILES]
    summaries.append(summarize_jobs(root / "lineage" / "jobs.yaml"))
    if (root / "_ops" / "ops-mapping.yaml").is_file():
        summaries.append(
            "_ops/ops-mapping.yaml : mapping d'exploitation (IP, hôtes physiques, chemins "
            "réels) DISPONIBLE dans ton snapshot — c'est TA source de vérité pour toute "
            "question d'infra (IP, hôte, FQDN, chemin). Tu Y AS ACCÈS : ne décline JAMAIS "
            "en prétendant que c'est « interne / non accessible / hors repo public ». "
            "Pour répondre, appelle lineage sur le système concerné "
            "(ex. lineage('timescaledb'), lineage('mariadb')) ou read_file _ops/ops-mapping.yaml, "
            "puis donne l'info concrète. (Canal privé : la confidentialité est déjà assurée, "
            "réponds normalement.)"
        )
    parts.append(
        "### Registres (résumé — outil lineage pour l'impact/les dépendances, read_file/grep pour le détail)\n"
        + "\n".join(summaries)
    )

    core_text = "\n\n".join(parts)
    blocks = [
        {"type": "text", "text": resolve_persona()},
        {"type": "text", "text": core_text, "cache_control": {"type": "ephemeral"}},
    ]
    # Errata des devs (!fix) — placés APRÈS le breakpoint de cache : leur ajout/
    # retrait n'invalide pas le préfixe caché (tools + persona + core).
    if corrections is not None:
        errata = corrections.render_system_block()
        if errata:
            blocks.append(errata)
    return blocks
