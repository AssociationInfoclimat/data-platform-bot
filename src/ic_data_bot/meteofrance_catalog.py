"""Vue Météo-France : rend les contrats ODCS de source (tags [source, meteofrance])
versionnés dans data-platform/contracts/. Plus de catalogue codé en dur — le contrat
fait foi, son customProperties.changelog porte l'historique des changements.

Chargé depuis ToolBox.root (racine du snapshot data-platform). AUTH et ERROR_TAXONOMY
restent des constantes transverses (savoir plateforme, hors versioning de schéma).
"""

from __future__ import annotations

from pathlib import Path

import yaml

AUTH = (
    "Auth OAuth2 unique : POST https://portail-api.meteofrance.fr/token, header "
    "`Authorization: Basic ${METEOFRANCE_APPLICATION_ID}`, body `grant_type=client_credentials` "
    "→ bearer token (TTL ~1 h). Souscriptions PAR APPLICATION. Gateways : "
    "`public-api.meteofrance.fr` (open data) et `api.meteofrance.fr` (commercial, ex. PIAF)."
)

ERROR_TAXONOMY = {
    "200/206": "servi — l'application est abonnée et l'endpoint répond (206 = Range bytes=0-0).",
    "401": "token expiré/invalide → refresh.",
    "403 900908": "l'application porteuse de la clé n'est PAS abonnée à cette API.",
    "404 No matching resource": "mauvais path/context/suffixe produit.",
    "404 NoSuchCoverage": "path OK mais coverageId faux / sur-annoncé / non servi.",
    "400/422": "route connue mais paramètres requis manquants (l'endpoint EXISTE).",
    "405": "méthode non supportée (utiliser GET + Range bytes=0-0).",
}


# ── Chargement des contrats de source ─────────────────────────────────────────

def _custom(doc: dict, key: str):
    for cp in doc.get("customProperties") or []:
        if cp.get("property") == key:
            return cp.get("value")
    return None


def _is_source(doc: dict) -> bool:
    tags = doc.get("tags") or []
    return isinstance(tags, list) and "source" in tags and "meteofrance" in tags


def _to_entry(doc: dict) -> dict:
    es = _custom(doc, "externalSource") or {}
    schema = doc.get("schema") or []
    props = (schema[0].get("properties") if schema else None) or []
    fields = [
        {
            "name": p.get("name", ""),
            "type": p.get("physicalType") or p.get("logicalType") or "",
            "unit": p.get("unit", ""),
            "desc": p.get("description", ""),
        }
        for p in props
    ]
    return {
        "id": es.get("apiId") or doc.get("name", ""),
        "label": doc.get("name", ""),
        "version": str(doc.get("version", "")),
        "host": es.get("host", ""),
        "context": es.get("context", ""),
        "url_template": es.get("urlTemplate", ""),
        "doc_url": es.get("docUrl", ""),
        "status": es.get("verified", ""),
        "auth": es.get("auth", "token"),
        "probe": es.get("probeUrl", ""),
        "notes": es.get("quirks") or [],
        "schema": {
            "note": es.get("unitsNote", ""),
            "descriptor": es.get("descriptor", ""),
            "quality": es.get("quality", ""),
            "fields": fields,
        },
        "changelog": _custom(doc, "changelog") or [],
    }


def load_sources(root) -> list[dict]:
    """Charge toutes les entrées de source depuis root/contracts/*.odcs.yaml."""
    d = Path(root) / "contracts"
    entries: list[dict] = []
    if not d.is_dir():
        return entries
    for fp in sorted(d.glob("*.odcs.yaml")):
        try:
            doc = yaml.safe_load(fp.read_text(encoding="utf-8", errors="replace"))
            if isinstance(doc, dict) and _is_source(doc):
                entries.append(_to_entry(doc))
        except (yaml.YAMLError, AttributeError, TypeError):
            continue
    return entries


def find(root, query: str):
    q = (query or "").strip().lower()
    if not q:
        return None
    entries = load_sources(root)
    for e in entries:
        if q == e["id"].lower():
            return e
    for e in entries:
        if q in e["id"].lower():
            return e
    for e in entries:
        if q in e["label"].lower() or q in e["context"].lower():
            return e
    return None


# ── Rendu ──────────────────────────────────────────────────────────────────

def overview(root) -> str:
    entries = load_sources(root)
    lines = ["# Catalogue des APIs Météo-France\n", AUTH, "", "## Sources versionnées", ""]
    lines += ["| API | Host | Context | Version | Statut |", "|---|---|---|---|---|"]
    for e in entries:
        lines.append(f"| `{e['id']}` | `{e['host']}` | `{e['context']}` | {e['version']} | {e['status']} |")
    if not entries:
        lines.append("| (aucune source chargée) | | | | |")
    lines += ["", "## Taxonomie d'erreurs (interprétation des probes)", ""]
    for code, meaning in ERROR_TAXONOMY.items():
        lines.append(f"- **{code}** : {meaning}")
    lines += [
        "",
        "→ `meteofrance_catalog(api=\"<id>\")` pour le contrat, `topic=\"schema\"` pour les "
        "champs/unités, `topic=\"changes\"` pour l'historique versionné, `probe=true` pour tester.",
    ]
    return "\n".join(lines)


def render_contract(e: dict) -> str:
    lines = [
        f"# {e['label']}  (`{e['id']}`)  — contrat v{e['version']}",
        f"- **Host** : `{e['host']}`",
        f"- **Context** : `{e['context']}`",
        f"- **URL** : `{e['url_template']}`",
        f"- **Statut** : {e['status']}",
        f"- **Auth** : {e['auth']}",
    ]
    if e.get("doc_url"):
        lines.append(f"- **Doc source (MF)** : {e['doc_url']}")
    lines += [
        "",
        "**Quirks / pièges :**",
    ]
    lines += [f"- {n}" for n in e["notes"]] or ["- (aucun)"]
    return "\n".join(lines)


def render_schema(e: dict) -> str:
    sc = e["schema"]
    lines = [f"# Schéma de données — {e['label']}  (`{e['id']}`, contrat v{e['version']})"]
    if sc.get("note"):
        lines += ["", sc["note"]]
    if sc.get("quality"):
        lines += ["", f"**Qualité** : {sc['quality']}"]
    fields = sc.get("fields") or []
    if fields:
        lines += ["", "| Champ | Type | Unité | Description |", "|---|---|---|---|"]
        for f in fields:
            lines.append(f"| `{f['name']}` | {f['type']} | {f['unit']} | {f['desc']} |")
    else:
        lines += ["", "(Pas de schéma tabulaire — cf. descripteur.)"]
    if sc.get("descriptor"):
        lines += ["", f"**Descripteur exhaustif** : {sc['descriptor']}"]
    return "\n".join(lines)


def _version_key(c: dict) -> tuple:
    """Clé de tri semver (tuple d'entiers), fallback (0,) si non parsable. On trie par
    VERSION décroissante (puis date) et non par date seule : le baseline initial est parfois
    rétro-daté avant un changement réel, donc la date ne reflète pas l'ordre des versions."""
    try:
        return tuple(int(p) for p in str(c.get("version", "0")).split("."))
    except ValueError:
        return (0,)


def render_changes(e: dict, since: str | None = None) -> str:
    """Historique versionné depuis customProperties.changelog. `since` = date ISO (incluse).
    Filtre par date ; tri par VERSION semver décroissante puis date (cf. _version_key)."""
    cl = list(e.get("changelog") or [])
    if since:
        cl = [c for c in cl if str(c.get("date", "")) >= since]
    if not cl:
        base = f"# Historique — {e['label']}  (`{e['id']}`)"
        if since:
            return base + f"\n\n(Aucun changement enregistré depuis {since}.)"
        return base + "\n\n(Aucun changement enregistré : version courante du contrat.)"
    cl.sort(key=lambda c: (_version_key(c), str(c.get("date", ""))), reverse=True)
    lines = [
        f"# Historique des changements — {e['label']}  (`{e['id']}`)",
        "",
        "| Version | Date | Sévérité | Type | Champs | Note |",
        "|---|---|---|---|---|---|",
    ]
    for c in cl:
        flds = ", ".join(c.get("fields") or []) or "—"
        lines.append(
            f"| {c.get('version', '?')} | {c.get('date', '?')} | **{c.get('severity', '?')}** "
            f"| {c.get('type', '?')} | {flds} | {c.get('note', '')} |"
        )
    return "\n".join(lines)


def not_found(root, query: str) -> str:
    ids = ", ".join(e["id"] for e in load_sources(root)) or "(aucune)"
    return (
        f"API Météo-France inconnue : « {query} ». APIs au catalogue : {ids}. "
        "Appelle `meteofrance_catalog()` sans argument pour la vue d'ensemble."
    )


# ── Probe (disponibilité) — inchangé ──────────────────────────────────────────

def interpret(status: int, snippet: str) -> str:
    s = snippet or ""
    if status in (200, 206):
        return "✅ servi — l'application est abonnée et l'endpoint répond."
    if status == 401:
        return "🔑 401 — token invalide/expiré (refresh nécessaire)."
    if status == 403 and "900908" in s:
        return "⛔ 403 900908 — l'application porteuse de la clé n'est PAS abonnée à cette API."
    if status == 403:
        return "⛔ 403 — accès refusé."
    if status == 404 and "NoSuchCoverage" in s:
        return "❓ 404 NoSuchCoverage — coverageId faux / sur-annoncé / non servi."
    if status == 404 and "indisponible" in s.lower():
        return "❓ 404 — path+format OK mais run/segment non servi."
    if status == 404:
        return "❓ 404 — mauvais path/context/suffixe (No matching resource)."
    if status in (400, 422):
        return f"🟡 {status} — route connue mais paramètres requis manquants (l'endpoint EXISTE)."
    if status == 405:
        return "🟡 405 — méthode non supportée (utiliser GET + Range bytes=0-0)."
    if status == 0:
        return "🌐 injoignable (réseau/DNS)."
    return f"statut {status}."


def render_probe(e: dict, client) -> str:
    url = e["probe"]
    needs_auth = e.get("auth", "token") != "none"
    res = client.probe(url, auth=needs_auth)
    status = res.get("status", 0)
    verdict = interpret(status, res.get("snippet", ""))
    lines = [
        f"# Probe — {e['label']}  (`{e['id']}`)",
        f"- **URL probée** : `{url}`",
        f"- **Statut HTTP** : {status}",
        f"- **Content-Type** : {res.get('content_type') or '—'}",
        f"- **Verdict** : {verdict}",
    ]
    snip = res.get("snippet")
    if snip and status not in (200, 206):
        lines.append(f"- **Corps** : {snip}")
    return "\n".join(lines)
