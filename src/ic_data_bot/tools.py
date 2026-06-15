from __future__ import annotations

import os
from pathlib import Path

MAX_GREP_MATCHES = 50

# Outil lineage : registres structurés à joindre, et bornes pour contenir le
# coût en tokens (le résultat part dans les messages, donc hors cache).
LINEAGE_FILES = [
    "catalog/catalog.yaml",
    "inventory/tables.yaml",
    "inventory/pipelines.yaml",
    "inventory/file-datasets.yaml",
    "inventory/external-sources.yaml",
    "inventory/storage-systems.yaml",
    "lineage/jobs.yaml",
    "_ops/ops-mapping.yaml",
]
MAX_LINEAGE_ENTRIES_PER_FILE = 6
MAX_LINEAGE_ENTRY_CHARS = 1200
MAX_LINEAGE_TOTAL_CHARS = 12_000


class ToolError(Exception):
    """Erreur d'outil renvoyée à Claude (is_error=True)."""


SCHEMAS = [
    {
        "name": "read_file",
        "description": (
            "Lit un fichier du snapshot data-platform (chemin relatif à la racine du "
            "snapshot). À utiliser pour le détail non présent dans le contexte : "
            "inventory/pipelines.yaml, inventory/tables.yaml, schémas SQL, audits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif, ex. inventory/tables.yaml"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Recherche une expression (regex Python) dans les fichiers du snapshot. "
            "Retourne les lignes correspondantes avec leur fichier. Utile pour localiser "
            "une table, un pipeline, un dataset."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Motif regex à chercher"},
                "glob": {"type": "string", "description": "Filtre glob optionnel, ex. inventory/*.yaml"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "lineage",
        "description": (
            "Analyse d'impact et dépendances : renvoie les entrées COMPLÈTES des "
            "registres (catalog, tables avec writers/readers, pipelines avec "
            "inputs/outputs, jobs lineage, contrats) qui référencent une table, un "
            "pipeline, un dataset ou un fichier. Appelle cet outil dès que la question "
            "porte sur l'impact d'un changement (renommer/supprimer une table ou une "
            "colonne, décommissionner un pipeline), sur qui lit ou écrit une table, ou "
            "sur les dépendances amont/aval d'un dataset. Préfère-le à grep pour ces "
            "questions : il renvoie les entrées YAML entières, pas des lignes isolées. "
            "IMPORTANT : avant de conclure sur l'état d'un writer/reader (actif, mort), "
            "croise avec les limitations du contrat concerné via read_file — les "
            "registres et les contrats peuvent documenter des doutes contradictoires."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nom (ou fragment) de table, pipeline, dataset — ex. 'foudre', 'cron.recup-blitzortung', 'V5_data'",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "kestra_recent",
        "description": (
            "État TEMPS RÉEL des jobs/pipelines : derniers événements Kestra "
            "(succès relayés dans #system-events, échecs dans #alerts), fenêtre "
            "glissante ~48 h. Appelle cet outil dès que la question porte sur le "
            "présent : « est-ce à jour ? », « ça a tourné ? », « derniers "
            "échecs ? », fraîcheur d'un dataset, état de la prod data. Le "
            "snapshot documentaire ne contient PAS cette information — lui seul "
            "décrit l'attendu, cet outil décrit le réel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Fragment du nom de flow/dataset à filtrer (ex. 'climato', 'refresh-materialized'). Vide = tous les événements récents.",
                }
            },
            "required": [],
        },
    },
]


class ToolBox:
    def __init__(self, root: Path, max_bytes: int = 60_000, kestra_log=None, public: bool = False):
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.kestra_log = kestra_log
        # Mode public (serveur MCP exposé) : interdit l'overlay confidentiel _ops/
        # (IP/hosts internes) dans read_file/grep/lineage. Défaut False = bot inchangé.
        self.public = public
        self._ops_dir = (self.root / "_ops").resolve()

    def _is_ops(self, candidate: Path) -> bool:
        return candidate == self._ops_dir or str(candidate).startswith(str(self._ops_dir) + os.sep)

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        if candidate != self.root and not str(candidate).startswith(str(self.root) + os.sep):
            raise ToolError(f"Chemin hors snapshot refusé : {rel}")
        if self.public and self._is_ops(candidate):
            raise ToolError(f"Chemin confidentiel refusé (mode public) : {rel}")
        return candidate

    def read_file(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.is_file():
            raise ToolError(f"Fichier introuvable : {path}")
        data = target.read_text(encoding="utf-8", errors="replace")
        if len(data) > self.max_bytes:
            return data[: self.max_bytes] + f"\n\n[… tronqué à {self.max_bytes} octets]"
        return data

    def grep(self, pattern: str, glob: str = "**/*") -> str:
        import re

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Regex invalide : {exc}")
        if "/" not in glob and "*" not in glob:
            glob = f"**/{glob}"
        matches: list[str] = []
        for fp in sorted(self.root.glob(glob)):
            if not fp.is_file():
                continue
            if self.public and self._is_ops(fp.resolve()):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rel = fp.relative_to(self.root)
                    matches.append(f"{rel}:{n}: {line.strip()[:200]}")
                    if len(matches) >= MAX_GREP_MATCHES:
                        matches.append(f"[… plus de {MAX_GREP_MATCHES} correspondances, affinez la recherche]")
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "Aucune correspondance."

    def lineage(self, name: str) -> str:
        """Jointure d'impact : entrées complètes des registres mentionnant `name`."""
        import yaml

        # Normalise tiret/underscore : les ids Kestra utilisent des underscores
        # (forum_rsync_uploads), le registre des tirets (forum-rsync-uploads) ; sans ça
        # un flow ne matche pas sa famille (cf. issue #1).
        needle = name.strip().lower().replace("-", "_")
        if not needle:
            raise ToolError("Nom vide.")
        sections: list[str] = []
        total = 0
        for rel in LINEAGE_FILES:
            if self.public and rel.startswith("_ops/"):
                continue  # overlay confidentiel exclu du mode public (MCP)
            path = self.root / rel
            if not path.is_file():
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
            except yaml.YAMLError:
                continue
            if isinstance(doc, list):
                collections = [doc]
            elif isinstance(doc, dict):
                collections = [v for v in doc.values() if isinstance(v, list)]
            else:
                continue
            hits: list[str] = []
            skipped = 0
            status_digest: list[str] = []  # tables non-actives, non plafonné (cf. ci-dessous)
            for coll in collections:
                for entry in coll:
                    if isinstance(entry, dict):
                        dump = yaml.safe_dump(entry, allow_unicode=True, sort_keys=False)
                    elif isinstance(entry, str):
                        dump = entry
                    else:
                        continue
                    if needle in dump.lower().replace("-", "_"):
                        # Le statut/usage RÉEL d'une table (mort/douteux + notes legacy)
                        # vit dans inventory/tables.yaml. Le plafond de MAX_LINEAGE_
                        # ENTRIES_PER_FILE droppait ces entrées quand le nom matchait
                        # beaucoup de tables (ex. « forums » → 11 tables, les ibf_*
                        # `status: mort` tombaient hors des 6 premières). On émet donc
                        # un digest des statuts NON-actifs, hors plafond, pour qu'ils
                        # soient toujours visibles.
                        if rel == "inventory/tables.yaml" and isinstance(entry, dict):
                            st = str(entry.get("status", "?"))
                            if st.lower() != "actif":
                                nm = str(entry.get("name", "?"))
                                note = " ".join(str(entry.get("notes") or "").split())
                                line = f"- `{nm}` → status: **{st}**"
                                if note:
                                    line += f" — {note[:220]}"
                                status_digest.append(line)
                        if len(hits) < MAX_LINEAGE_ENTRIES_PER_FILE:
                            hits.append(dump.strip()[:MAX_LINEAGE_ENTRY_CHARS])
                        else:
                            skipped += 1
            if hits:
                block = f"### {rel} ({len(hits) + skipped} entrée(s))\n" + "\n---\n".join(hits)
                if skipped:
                    block += f"\n[… {skipped} autre(s) entrée(s) — affine avec grep]"
                if status_digest:
                    block = (
                        "### ⚠️ STATUTS TABLE non-actifs (inventory/tables.yaml) — "
                        "FONT AUTORITÉ pour juger si une table est morte/douteuse/utilisée, "
                        "AVANT tout statut de contrat (un contrat `draft` ne rend pas une "
                        "table morte « active ») :\n"
                        + "\n".join(status_digest[:20])
                        + "\n\n"
                        + block
                    )
                sections.append(block)
                total += len(block)
                if total > MAX_LINEAGE_TOTAL_CHARS:
                    sections.append("[… résultat tronqué — affine la recherche]")
                    break
        contracts_dir = self.root / "contracts"
        if contracts_dir.is_dir():
            mentioned = [
                f"contracts/{fp.name}"
                for fp in sorted(contracts_dir.glob("*.odcs.yaml"))
                if not fp.name.startswith("_")
                and needle in fp.read_text(encoding="utf-8", errors="replace").lower().replace("-", "_")
            ]
            if mentioned:
                sections.append(
                    "### ⚠️ Contrats concernés — ÉTAPE OBLIGATOIRE avant de répondre :\n"
                    "lis ces contrats via read_file pour la nuance usage/limitations "
                    "(rétention, writers présumés, pièges). NB : le statut de contrat "
                    "(`draft`/`active`) renseigne la contractualisation, PAS si la table "
                    "est vivante — pour ça, le `status` TABLE de inventory/tables.yaml "
                    "ci-dessus fait foi (mort/douteux prime). Ne déclare jamais un "
                    "writer/reader « actif » sans avoir vérifié l'inventaire ET les "
                    "limitations du contrat.\n"
                    + "\n".join(mentioned)
                )
        if not sections:
            return (
                f"Aucune référence à « {name} » dans les registres. "
                "Essaie grep (code/doc libre) ou vérifie l'orthographe."
            )
        return "\n\n".join(sections)

    def dispatch(self, name: str, tool_input: dict) -> str:
        if name == "read_file":
            return self.read_file(tool_input["path"])
        if name == "grep":
            return self.grep(tool_input["pattern"], tool_input.get("glob") or "**/*")
        if name == "lineage":
            return self.lineage(tool_input["name"])
        if name == "kestra_recent":
            if self.kestra_log is None:
                raise ToolError("Événements Kestra non configurés sur ce déploiement.")
            return self.kestra_log.recent(tool_input.get("query") or "")
        raise ToolError(f"Outil inconnu : {name}")
