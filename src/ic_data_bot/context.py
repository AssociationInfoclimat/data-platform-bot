from __future__ import annotations

from pathlib import Path

import yaml

SYSTEM_PERSONA = (
    "Tu es le manager de la donnée d'Infoclimat. Tu réponds aux questions des "
    "développeurs sur la plateforme data : catalogue, contrats ODCS, lineage, "
    "inventaire (tables/pipelines/sources/stockage), migration MariaDB→TimescaleDB, "
    "gouvernance. Tu te bases UNIQUEMENT sur le snapshot data-platform fourni ci-dessous "
    "et sur tes outils read_file/grep/lineage. Si une information n'y figure pas, dis-le ; "
    "n'invente rien. Cite tes sources (chemin de fichier).\n"
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
    "jamais à une source. Pour un volume, cite au mieux le dernier audit "
    "`audits/volumetrie/` AVEC sa date (c'est un snapshot daté, pas le live) et "
    "précise que le compte exact actuel exige une requête SQL en prod.\n"
    "Si la question sort du périmètre data, décline poliment et oriente vers le bon canal.\n\n"
    "FORMAT — tu réponds dans Discord, ta réponse est tronquée au-delà de 1900 "
    "caractères : vise moins de 1500 caractères. Va à l'essentiel en premier "
    "(réponse de première intention), puis propose d'approfondir si pertinent. "
    "JAMAIS de tableaux Markdown (Discord ne les rend pas) : utilise des listes à "
    "puces, du **gras**, du `code inline` et des blocs ``` pour le YAML/SQL. "
    "Réponds en français."
)

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
        {"type": "text", "text": SYSTEM_PERSONA},
        {"type": "text", "text": core_text, "cache_control": {"type": "ephemeral"}},
    ]
    # Errata des devs (!fix) — placés APRÈS le breakpoint de cache : leur ajout/
    # retrait n'invalide pas le préfixe caché (tools + persona + core).
    if corrections is not None:
        errata = corrections.render_system_block()
        if errata:
            blocks.append(errata)
    return blocks
