from __future__ import annotations

import os
import re
from pathlib import Path

MAX_GREP_MATCHES = 50

# ── Caviardage de secrets ───────────────────────────────────────────────────
# Le code legacy indexé par search_code contient des secrets en dur (mots de passe,
# clés API, tokens, clés privées). On les masque DANS l'outil (déterministe) avant de
# renvoyer le moindre extrait — on ne se fie pas au modèle pour s'auto-censurer.
_S = "‹secret-rédacté›"
# Mots-clés « nom de secret ». Le mot-clé peut apparaître en SOUS-CHAÎNE d'un identifiant
# (ex. USER_SALT1, MYSQL_API_KEY2) : les règles « clé nue » l'encadrent par [A-Za-z0-9_]* —
# d'où l'absence ici de fragments trop fréquents (auth/sign nus) qui masqueraient `author`,
# `assignment`, etc. Les secrets nommés ainsi (INT_AUTH_API…) sont rattrapés par la règle
# « valeur à haute entropie » plus bas, fondée sur la VALEUR, pas le nom.
_SECRET_KEY = (r"(?:passphrase|passwd|password|pwd|mot[_ ]?de[_ ]?passe|mdp|secret|"
               r"api[_-]?key|apikey|access[_-]?key|secret[_-]?key|auth[_-]?token|auth[_-]?key|"
               r"access[_-]?token|oauth|token|bearer|credentials?|private[_-]?key|salt|pepper|"
               r"nonce|cipher|signature|app[_-]?id|application[_-]?id|appid|"
               r"client[_-]?id|client[_-]?secret|consumer[_-]?key|consumer[_-]?secret|"
               r"signing[_-]?key|hmac[_-]?key|license[_-]?key|encryption[_-]?key|crypt[_-]?key)")
_STRONG_KEY = (r"(?:passphrase|passwd|password|pwd|mdp|secret|api[_-]?key|apikey|access[_-]?key|"
               r"secret[_-]?key|auth[_-]?key|private[_-]?key|credentials?|salt|application[_-]?id|"
               r"app[_-]?id|client[_-]?secret|consumer[_-]?secret|encryption[_-]?key|crypt[_-]?key)")

# Valeur quotée « qui ressemble à un secret » indépendamment du nom de la constante : longue
# chaîne (≥12) sans espace ni séparateur de chemin, casse mixte, et un chiffre OU un symbole
# « secret ». Couvre les secrets aux noms exotiques (INT_AUTH_API='Fq3@kPx7Rmn',
# EXT_AUTH1='Wd7HmZ3Btvq9'). GARDE ANTI-FAUX-POSITIF : en l'absence de symbole, on exclut
# les identifiants « prononçables » (un segment minuscule ≥5 = un mot, ex. MyClassNameV2Handler,
# UserProfileV2Tpl, sha…) pour ne pas masquer du code légitime que search_code doit montrer.
# Exclut aussi URLs/chemins/nombres (charset) et le tout-majuscule/tout-minuscule (casse mixte).
_ENTROPY_RX = re.compile(r"""((?:=>|[:=])\s*)(['"])([^'"\n]{12,})\2""")
_ENTROPY_CHARSET = re.compile(r"[A-Za-z0-9+/=_@!#$%^&*\-]{12,}\Z")
_SECRET_SYM = re.compile(r"[+/=@!#$%^&*]")  # symboles « secret » (hors - et _, trop courants en code)
_WORDY_LC_RUN = 5  # un segment minuscule de cette longueur ⇒ mot prononçable, pas un secret


def _looks_like_secret(val: str) -> bool:
    """Heuristique de VALEUR (nom de constante ignoré) : longue, casse mixte, entropie élevée,
    sans ressembler à un identifiant prononçable. Sens d'échec choisi = sur-masquer (sûr)."""
    if not (_ENTROPY_CHARSET.match(val)
            and re.search(r"[a-z]", val) and re.search(r"[A-Z]", val)):
        return False
    has_sym = bool(_SECRET_SYM.search(val))
    if not (has_sym or re.search(r"[0-9]", val)):
        return False
    if not has_sym:
        longest_lc = max((len(r) for r in re.findall(r"[a-z]+", val)), default=0)
        if longest_lc >= _WORDY_LC_RUN:
            return False
    return True


def _redact_entropy(m: "re.Match") -> str:
    if _looks_like_secret(m.group(3)):
        return f"{m.group(1)}{m.group(2)}{_S}{m.group(2)}"
    return m.group(0)


_SECRET_RULES = [
    # define('XXX_PASSWORD', 'valeur') / define("API_KEY", "valeur")  (PHP)
    (re.compile(r"""(define\s*\(\s*['"][^'"]*(?:pass|secret|key|token|pwd|mdp|cred)[^'"]*['"]\s*,\s*['"])[^'"]{2,}(['"])""", re.I), r"\1" + _S + r"\2"),
    # clé quotée : 'password' => 'valeur'  |  "api_key": "valeur"
    (re.compile(r"""((['"])""" + _SECRET_KEY + r"""\2\s*(?:=>|:)\s*(['"]))[^'"\n]{2,}(['"])""", re.I), r"\1" + _S + r"\4"),
    # clé nue, valeur quotée : password = 'valeur' | USER_SALT1 = '...' (mot-clé en sous-chaîne ; préfixe DB_/MYSQL_ OK)
    (re.compile(r"""((?<![A-Za-z0-9_])[A-Za-z0-9_]*""" + _SECRET_KEY + r"""[A-Za-z0-9_]*\s*(?:=>|[:=])\s*(['"]))[^'"\n]{2,}\2""", re.I), r"\1" + _S + r"\2"),
    # clé forte, valeur nue (env/.ini) : DB_PASSWORD=xxxx | USER_SALT1=xxxx
    (re.compile(r"""((?<![A-Za-z0-9_])[A-Za-z0-9_]*""" + _STRONG_KEY + r"""[A-Za-z0-9_]*\s*[:=]\s*)[^\s"'`,;)]{5,}""", re.I), r"\1" + _S),
    # valeur quotée à haute entropie (nom de constante quelconque)
    (_ENTROPY_RX, _redact_entropy),
    # identifiants dans une URL/DSN : scheme://user:motdepasse@host
    (re.compile(r"([a-zA-Z][\w+.\-]*://[^\s:@/]+:)[^\s@/]{2,}(@)"), r"\1" + _S + r"\2"),
    # préfixes de jetons connus
    (re.compile(r"\b(?:sk-ant-[\w-]{6,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[baprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{20,})\b"), _S),
    # blocs de clés privées PEM
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "‹clé-privée-rédactée›"),
]


def redact_secrets(text: str) -> str:
    """Masque les secrets en dur (mots de passe, clés, tokens, clés privées) d'un extrait."""
    if not text:
        return text
    for rx, repl in _SECRET_RULES:
        text = rx.sub(repl, text)
    return text

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

# Volumétrie auditée (CSV datés) et snapshots DDL (gzip).
VOLUMETRIE_DIR = "audits/volumetrie"
SCHEMA_FILES = ["schemas/mariadb/schema.sql.gz", "schemas/timescaledb/schema.sql.gz"]
MAX_SCHEMA_CHARS = 6_000


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
    {
        "name": "volumetrie",
        "description": (
            "Volumétrie AUDITÉE d'une table : nombre de lignes et taille au dernier audit "
            "DATÉ (audits/volumetrie/), pas le live. Appelle-le pour « combien de lignes / "
            "quelle taille fait la table X », un ordre de grandeur. Précise toujours que "
            "c'est un snapshot daté ; le compte exact actuel exige une requête SQL en prod. "
            "N'invente jamais un volume — utilise CET outil ou dis que tu ne sais pas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nom (ou fragment) de table — ex. 'foudre', 'Infrahoraire'"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Recherche SÉMANTIQUE dans le CODE SOURCE des repos Infoclimat "
            "(site-infoclimat, infrapilot, modeles-ncl/php, python-climate-services, "
            "data-platform…) via un index vectoriel codestral-embed. Pour « où / comment "
            "est implémenté X dans le code », retrouver la fonction ou le fichier qui fait "
            "Y, par le SENS et non le mot-clé. Complémentaire de grep (lexical, limité au "
            "snapshot data-platform) : ici c'est le code applicatif de tous les repos. "
            "Renvoie les extraits les plus proches avec repo/chemin:lignes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question en langage naturel sur le code"},
                "repo": {"type": "string", "description": "Limiter à un repo (ex. site-infoclimat) — optionnel"},
                "k": {"type": "integer", "description": "Nombre d'extraits à renvoyer (défaut 6)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "schema",
        "description": (
            "DDL RÉEL d'une table (CREATE TABLE) depuis les snapshots de schéma "
            "(schemas/{mariadb,timescaledb}/schema.sql.gz) : types de colonnes exacts, clés, "
            "index. Appelle-le pour « quel type a la colonne X », « le schéma physique de la "
            "table Y » — vérité physique, complémentaire des contrats ODCS (qui, eux, donnent "
            "l'usage et les unités). Donne le nom EXACT de la table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nom exact de la table — ex. 'foudre', 'Infrahoraire'"},
            },
            "required": ["name"],
        },
    },
]


class ToolBox:
    def __init__(self, root: Path, max_bytes: int = 60_000, kestra_log=None, public: bool = False,
                 secret_scrub=None):
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.kestra_log = kestra_log
        # Mode public (serveur MCP exposé) : interdit l'overlay confidentiel _ops/
        # (IP/hosts internes) dans read_file/grep/lineage. Défaut False = bot inchangé.
        self.public = public
        # Guardrail modèle optionnel (text->texte caviardé) appliqué à la SORTIE de
        # search_code, après le caviardage regex. Filet sémantique pour les secrets aux
        # noms exotiques que redact_secrets() ne connaît pas. Injecté par bot.py/mcp_server.py.
        self.secret_scrub = secret_scrub
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

    def volumetrie(self, name: str) -> str:
        """Volumétrie auditée (nb lignes + taille) d'une table, par audit daté. Snapshot,
        pas le live : le décompte exact actuel exige une requête SQL en prod."""
        import csv
        import re

        needle = name.strip().lower().replace("-", "_")
        if not needle:
            raise ToolError("Nom vide.")
        vdir = self.root / VOLUMETRIE_DIR
        rows = []  # (date, system, db, table, row_estimate, total_bytes)
        for fp in sorted(vdir.glob("*.csv")) if vdir.is_dir() else []:
            m = re.search(r"(\d{8})", fp.name)
            date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else "?"
            try:
                reader = csv.DictReader(fp.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
            for r in reader:
                tbl = r.get("table") or ""
                if needle in tbl.lower().replace("-", "_"):
                    rows.append((date, r.get("system", ""), r.get("database", ""), tbl,
                                 r.get("row_estimate", ""), r.get("total_bytes", "")))
        if not rows:
            return (f"Aucune volumétrie auditée pour « {name} » dans {VOLUMETRIE_DIR}/. "
                    "Le snapshot ne donne pas de décompte ; requête SQL en prod pour l'exact.")
        rows.sort(key=lambda x: x[0], reverse=True)
        out = ["Volumétrie AUDITÉE (snapshot DATÉ, pas le live ; exact actuel = requête SQL prod) :"]
        for date, sysn, db, tbl, rowest, totb in rows[:20]:
            try:
                gib = f"{int(totb) / 1024 ** 3:.1f} GiB"
            except (ValueError, TypeError):
                gib = "?"
            try:
                n = f"{int(rowest):,}".replace(",", " ")
            except (ValueError, TypeError):
                n = rowest or "?"
            out.append(f"- {sysn}://{db}/{tbl} : {n} lignes (~{gib}) — audit {date}")
        return "\n".join(out)

    def schema(self, name: str) -> str:
        """DDL CREATE TABLE de `name` depuis les snapshots schemas/*/schema.sql.gz."""
        import gzip
        import re

        tbl = name.strip()
        if not tbl:
            raise ToolError("Nom vide.")
        pat = re.compile(r'CREATE TABLE[^;]*?[`"]' + re.escape(tbl) + r'[`"][^;]*?;',
                         re.IGNORECASE | re.DOTALL)
        blocks = []
        for rel in SCHEMA_FILES:
            fp = self.root / rel
            if not fp.is_file():
                continue
            try:
                ddl = gzip.decompress(fp.read_bytes()).decode("utf-8", errors="replace")
            except (OSError, gzip.BadGzipFile):
                continue
            for m in pat.finditer(ddl):
                blocks.append(f"### {rel}\n{m.group(0).strip()[:MAX_SCHEMA_CHARS]}")
        if not blocks:
            return f"Aucun DDL pour « {name} » dans {', '.join(SCHEMA_FILES)} (vérifie le nom exact)."
        return "\n\n".join(blocks)[: MAX_SCHEMA_CHARS * 2]

    def search_code(self, query: str, repo: str | None = None, k: int = 6) -> str:
        """Recherche sémantique dans le code source (index vectoriel code_index de
        data-platform/tools, réutilisé via search_code()). Le code applicatif vient de
        repos PRIVÉS : refusé en mode public (serveur MCP) sauf opt-in CODE_INDEX_PUBLIC.

        Interrupteur maître `CODE_INDEX_ENABLED` : désactivé par défaut. L'import de
        lancedb (back-end vectoriel) embarque du code natif AVX2 qui plante par SIGILL
        sur un CPU sans AVX2 (un signal NON rattrapable → crash du process). On
        n'importe donc lancedb QUE si l'opérateur a activé le flag après avoir vérifié
        que la plateforme le supporte."""
        if os.environ.get("CODE_INDEX_ENABLED", "").lower() not in ("1", "true", "yes"):
            raise ToolError("Recherche de code désactivée (CODE_INDEX_ENABLED non défini ; "
                            "le back-end vectoriel requiert un CPU avec AVX2).")
        if self.public and os.environ.get("CODE_INDEX_PUBLIC", "").lower() not in ("1", "true", "yes"):
            raise ToolError("Recherche de code désactivée en mode public (code applicatif "
                            "des repos privés non exposé).")
        if not query or not query.strip():
            raise ToolError("Requête vide.")
        # Le module code_index vit dans data-platform/tools (présent dans le snapshot, ou
        # via CODE_INDEX_TOOLS_DIR). L'index LanceDB et MISTRAL_API_KEY sont lus par
        # code_index.config (CODE_INDEX_DIR, MISTRAL_API_KEY).
        import sys

        tools_dir = os.environ.get("CODE_INDEX_TOOLS_DIR") or str(self.root / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        try:
            from code_index import search_code as _search
        except ImportError:
            raise ToolError("Index de code indisponible : module code_index introuvable "
                            "(définir CODE_INDEX_TOOLS_DIR vers data-platform/tools).")
        try:
            k = max(1, min(int(k or 6), 20))
            results = _search(query, k=k, repos=[repo] if repo else None)
        except Exception as exc:  # noqa: BLE001 — surface l'erreur à l'agent
            raise ToolError(f"Recherche de code impossible : {type(exc).__name__}: {exc}")
        if not results:
            return "Aucun extrait pertinent (index non construit pour ce périmètre ?)."
        blocks = []
        for r in results:
            snippet = redact_secrets("\n".join(r.text.splitlines()[:18]))
            blocks.append(f"### {r.location} ({r.lang}, distance {r.score:.3f})\n{snippet}")
        out = "\n\n".join(blocks)
        # Filet sémantique : un petit LLM repère les secrets que le regex ignore (noms
        # exotiques). Si indispo/erreur, on garde la sortie regex (jamais de fuite au-delà).
        if self.secret_scrub:
            out = self.secret_scrub(out)
        return out

    def dispatch(self, name: str, tool_input: dict) -> str:
        # Point d'étranglement UNIQUE : toute sortie d'outil passe par redact_secrets avant
        # d'atteindre le modèle (read_file/grep ne caviardaient rien auparavant — fuite).
        # redact_secrets est idempotent : la double passe sur search_code (déjà rédacté +
        # scrub LLM) est sans effet.
        return redact_secrets(self._dispatch(name, tool_input))

    def _dispatch(self, name: str, tool_input: dict) -> str:
        if name == "read_file":
            return self.read_file(tool_input["path"])
        if name == "grep":
            return self.grep(tool_input["pattern"], tool_input.get("glob") or "**/*")
        if name == "lineage":
            return self.lineage(tool_input["name"])
        if name == "volumetrie":
            return self.volumetrie(tool_input["name"])
        if name == "schema":
            return self.schema(tool_input["name"])
        if name == "search_code":
            return self.search_code(tool_input["query"], tool_input.get("repo") or None,
                                    int(tool_input.get("k") or 6))
        if name == "kestra_recent":
            if self.kestra_log is None:
                raise ToolError("Événements Kestra non configurés sur ce déploiement.")
            return self.kestra_log.recent(tool_input.get("query") or "")
        raise ToolError(f"Outil inconnu : {name}")
