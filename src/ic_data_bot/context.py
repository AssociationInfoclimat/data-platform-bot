from __future__ import annotations

from pathlib import Path

import yaml

SYSTEM_PERSONA = (
    "Tu es le manager de la donnée d'Infoclimat. Tu réponds aux questions des "
    "développeurs sur la plateforme data : catalogue, contrats ODCS, lineage, "
    "inventaire (tables/pipelines/sources/stockage), migration MariaDB→TimescaleDB, "
    "gouvernance. Tu te bases UNIQUEMENT sur le snapshot data-platform fourni ci-dessous "
    "et sur tes outils read_file/grep/lineage. Si une information n'y figure pas, dis-le ; "
    "n'invente rien. Cite tes sources (chemin de fichier). Si la question sort du "
    "périmètre data, décline poliment et oriente vers le bon canal.\n\n"
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


def summarize_contract(path: Path) -> str:
    """Résumé compact d'un contrat ODCS : identité, but, tables et colonnes.
    Les détails (usage, limitations, pièges, types) se lisent via read_file —
    inliner les contrats entiers coûtait ~17k tokens de préfixe par question."""
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
        cols = ", ".join(
            p.get("name", "?") for p in table.get("properties") or [] if isinstance(p, dict)
        )
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
        index = [
            summarize_contract(fp)
            for fp in sorted(contracts_dir.glob("*.odcs.yaml"))
            if not fp.name.startswith("_")
        ]
        if index:
            parts.append(
                "### Index des contrats ODCS — pour les détails d'un contrat "
                "(usage, limitations, pièges, types, descriptions de colonnes), "
                "TOUJOURS lire le fichier complet via read_file avant de répondre.\n\n"
                + "\n\n".join(index)
            )

    # Résumés chiffrés des gros registres + jobs lineage
    summaries = [summarize_registry(root / rel) for rel in REGISTRY_FILES]
    summaries.append(summarize_jobs(root / "lineage" / "jobs.yaml"))
    if (root / "_ops" / "ops-mapping.yaml").is_file():
        summaries.append(
            "_ops/ops-mapping.yaml : mapping ops INTERNE (IP, hôtes, chemins réels) — "
            "absent du repo public ; source prioritaire pour les questions "
            "d'exploitation, joint automatiquement par l'outil lineage"
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
