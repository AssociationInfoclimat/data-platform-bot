"""Catalogue de référence des APIs Météo-France (contrats d'URL + schéma de données).

Source de vérité côté bot pour répondre SANS re-prober/re-deviner :
- « quelle est l'URL / le contrat de l'API X » (host, context, gabarit d'URL, quirks),
- « quels champs/paramètres renvoie l'API X, avec quelles unités » (dictionnaire de données),
- « est-ce servi en ce moment » (interprétation des codes de probe, cf. meteofrance_api).

Dérivé du skill `meteofrance-apis` et des descripteurs officiels MF
(telechargement-climatologie-portail-api-meteofrance/docs/mf/, data-samples/). Tenir à jour
quand une API est vérifiée en live. NE contient JAMAIS de secret (l'APPLICATION_ID vit en env).

⚠️ Piège transverse : les réponses DPObs/DPPaquetObs sont en UNITÉS SI BRUTES (température en
Kelvin, pression en Pascal). Les tables persistées (tool `schema`) et la climato sont en unités
conventionnelles (°C, 1/10). Toujours signaler cette divergence à l'utilisateur.
"""

from __future__ import annotations

# ── Communs ──────────────────────────────────────────────────────────────────

AUTH = (
    "Auth OAuth2 unique : POST https://portail-api.meteofrance.fr/token, header "
    "`Authorization: Basic ${METEOFRANCE_APPLICATION_ID}`, body `grant_type=client_credentials` "
    "→ bearer token (TTL ~1 h). Souscriptions PAR APPLICATION (pas par compte). Gateways : "
    "`public-api.meteofrance.fr` (open data) et `api.meteofrance.fr` (commercial, ex. PIAF) — "
    "ne pas forcer public-api si le devportal indique api.meteofrance.fr."
)

ERROR_TAXONOMY = {
    "200/206": "servi — l'application est abonnée et l'endpoint répond (206 = Range bytes=0-0).",
    "401": "token expiré/invalide → refresh.",
    "403 900908": "l'application porteuse de la clé n'est PAS abonnée à cette API (≠ token invalide).",
    "404 No matching resource": "mauvais path/context/suffixe produit.",
    "404 NoSuchCoverage": "path OK mais coverageId faux / sur-annoncé par GetCapabilities / non servi.",
    "404 indisponible": "(Paquets) path+format OK mais run/segment non encore servi.",
    "400/422": "route connue mais paramètres requis manquants ou format invalidé (l'endpoint EXISTE).",
    "405": "méthode non supportée (ex. HEAD → 405 ; utiliser GET + Range bytes=0-0).",
}

# Note d'unités réutilisée par les entrées d'observation.
_SI_NOTE = (
    "⚠️ Unités SI BRUTES dans la réponse API : température en KELVIN, pression en PASCAL. "
    "Les tables persistées (tool `schema`) convertissent en °C / hPa."
)

# ── Catalogue ────────────────────────────────────────────────────────────────
# Chaque entrée : id, label, host, context, url_template, status, auth, probe (url),
# notes (quirks), schema {note, descriptor, quality, fields:[{name,type,unit,desc}]}.

CATALOG = [
    {
        "id": "DPObs",
        "label": "Observations stations (DPObs)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/DPObs/v1",
        "url_template": "/public/DPObs/v1/station/{horaire|infrahoraire-6m}?id_station={id}&format=json",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/DPObs/v1/liste-stations",
        "notes": [
            "Stations RADOME (2000+), 24 dernières heures. CSV ou JSON (format=json).",
            "/liste-stations (CSV : Id_station, Id_omm, Nom, lat/lon/alt, Pack), /station/horaire, "
            "/station/infrahoraire-6m (pas 6 min), /synop, /bouees.",
        ],
        "schema": {
            "note": _SI_NOTE,
            "descriptor": "docs/mf/observations/{horaire,infrahoraire}.csv (descripteur officiel MF complet)",
            "quality": "Pas d'indicateur qualité Q* sur le flux temps réel DPObs (≠ climato).",
            "fields": [
                {"name": "geo_id_insee", "type": "TEXT", "unit": "ddnnnpp", "desc": "ID point (département+commune Insee+précision site)"},
                {"name": "lat / lon", "type": "REAL", "unit": "degré", "desc": "position du poste"},
                {"name": "reference_time", "type": "TEXT", "unit": "ISO 8601 UTC", "desc": "production de la donnée"},
                {"name": "validity_time", "type": "TEXT", "unit": "ISO 8601 UTC", "desc": "validité (clé temporelle)"},
                {"name": "t / td", "type": "REAL", "unit": "K (Kelvin)", "desc": "température / point de rosée sous abri"},
                {"name": "tx / tn", "type": "REAL", "unit": "K", "desc": "T max / min sur la période (horaire)"},
                {"name": "u", "type": "INTEGER", "unit": "%", "desc": "humidité relative"},
                {"name": "dd", "type": "INTEGER", "unit": "degré (rose 360)", "desc": "direction du vent moyen"},
                {"name": "ff", "type": "REAL", "unit": "m/s", "desc": "vent moyen à 10 m"},
                {"name": "fxi10 / fxi", "type": "REAL", "unit": "m/s", "desc": "rafale max instantanée"},
                {"name": "rr1 / rr_per", "type": "REAL", "unit": "mm", "desc": "précipitations (1 h horaire / 6 min infra)"},
                {"name": "t_10 / t_20 / t_50 / t_100", "type": "REAL", "unit": "K", "desc": "température du sol à 10/20/50/100 cm"},
                {"name": "vv", "type": "INTEGER", "unit": "m", "desc": "visibilité horizontale"},
                {"name": "pres / pmer", "type": "REAL", "unit": "Pa (Pascal)", "desc": "pression station / niveau mer"},
                {"name": "insolh", "type": "REAL", "unit": "min", "desc": "durée d'insolation sur la période"},
                {"name": "ray_glo01", "type": "REAL", "unit": "J/m²", "desc": "rayonnement global sur la période"},
            ],
        },
    },
    {
        "id": "DPPaquetObs",
        "label": "Paquets d'observations (DPPaquetObs)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/DPPaquetObs/v1",
        "url_template": "/public/DPPaquetObs/v1/paquet/{horaire|infrahoraire-6m} | /paquet/stations/{horaire|infrahoraire-6m}",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/DPPaquetObs/v1/liste-stations",
        "notes": [
            "Paquets groupés : 24 h par station/département, ou tous stations pour un horaire.",
            "/paquet/horaire → 400 sans paramètres requis (probablement id-departement/date) = route OK.",
        ],
        "schema": {
            "note": _SI_NOTE + " Mêmes mnémoniques que DPObs (geo_id_insee, t, ff, rr1…).",
            "descriptor": "docs/mf/observations/ (mêmes champs que DPObs, groupés par paquet)",
            "quality": "Idem DPObs (pas de Q*).",
            "fields": [],
        },
    },
    {
        "id": "DPRadar",
        "label": "Données radar (DPRadar)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/DPRadar",
        "url_template": "/public/DPRadar/mosaiques/{zone}/observations/{observation}/produit | /stations/{id}/...",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/DPRadar/mosaiques",
        "notes": [
            "Liens canoniques SANS /v1/ (alias /v1/ accepté). Fréquence 5 min.",
            "Zones via /mosaiques (METROPOLE, ANTILLES…) — `FRANCE` → 404. Produits : réflectivité, lame d'eau, PAM, PAG.",
        ],
        "schema": {
            "note": "Produits radar (HDF5/BUFR/PNG selon /produit) ; pas un schéma tabulaire.",
            "descriptor": "/liste-stations (CSV : id, nom, ref produit PAG/PAM, tours d'antenne)",
            "quality": "",
            "fields": [],
        },
    },
    {
        "id": "DPPaquetRadar",
        "label": "Paquets radar (DPPaquetRadar)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/DPPaquetRadar/v1",
        "url_template": "/public/DPPaquetRadar/v1/mosaique/paquet | /station/paquet",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/DPPaquetRadar/v1/liste-stations",
        "notes": [
            "/mosaique/paquet = tar gzip du ¼h récent (~16 Mo). /station/paquet → 422 sans id_station = route OK.",
        ],
        "schema": {"note": "Archive tar gzip (binaire).", "descriptor": "", "quality": "", "fields": []},
    },
    {
        "id": "AROME-WCS",
        "label": "Modèle AROME — WCS (métropole 0.025°/0.01°)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/arome/1.0/wcs/{coll}",
        "url_template": "/public/arome/1.0/wcs/{coll}/{GetCapabilities|DescribeCoverage|GetCoverage}?service=WCS&version=2.0.1",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/arome/1.0/wcs/MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS/GetCapabilities?service=WCS&version=2.0.1",
        "notes": [
            "coll : MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS (0.025°) ou -001- (0.01°). coverageId={VAR}___{run} (run avec POINTS : 2026-06-03T18.00.00Z).",
            "GetCoverage : subset=time(...) SANS quotes, subset=height(2), format=application/wmo-grib, 1 message/requête. Grille N→S (flip).",
            "Cumuls de précip : suffixe période `_PT1H`/`_PT3H`/`_P1D` OBLIGATOIRE sinon 404. Validation par DescribeCoverage (GetCapabilities sur-annonce).",
        ],
        "schema": {
            "note": "Produits diagnostiques (réflectivité dBZ, CAPE/CIN, visibilité, foudre, ptype) + cumuls précip via _PTxH.",
            "descriptor": "GetCapabilities → liste des coverageId réellement servis (valider par DescribeCoverage)",
            "quality": "",
            "fields": [
                {"name": "TOTAL_PRECIPITATION", "type": "GRIB", "unit": "kg/m² (mm)", "desc": "cumul précip total (suffixe _PTxH)"},
                {"name": "TOTAL_SNOW_PRECIPITATION", "type": "GRIB", "unit": "kg/m²", "desc": "cumul neige"},
                {"name": "TOTAL_PRECIPITATION_RATE", "type": "GRIB", "unit": "kg/m²/s", "desc": "intensité précip instantanée"},
                {"name": "CAPE / CIN", "type": "GRIB", "unit": "J/kg", "desc": "énergie convective"},
                {"name": "(NON servi)", "type": "—", "unit": "—", "desc": "HAIL, GRAUPEL, LIGHTNING_*_CUMULATED → 404 (grésil cumul = Paquets `tgrp`)"},
            ],
        },
    },
    {
        "id": "AROME-Paquets",
        "label": "Modèle AROME — Paquets (DPPaquetAROME, métropole)",
        "host": "public-api.meteofrance.fr",
        "context": "/previnum/DPPaquetAROME/v1",
        "url_template": "/previnum/DPPaquetAROME/v1/models/AROME/grids/{0.025|0.01}/packages/{PKG}/productARO?referencetime={RUN}&time={SEGMENT}&format=grib2",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/previnum/DPPaquetAROME/v1/models/AROME/grids/0.025/packages",
        "notes": [
            "Suffixe produit `productARO`. Échéances en SEGMENTS 6 h : 00H06H, 07H12H, … (≠ AROME-OM). Runs 8/j (00,03,…,21 UTC).",
            "Paquets SP1/SP2/SP3 (surface), IP1-5/HP1 (isobares). Découverte des time= servis : GET .../packages/{PKG}?referencetime={RUN}.",
            "Volume : IP1 segment 00H06H ≈ 488 Mo (24 niveaux × 5 vars) → filtrer au décodage.",
        ],
        "schema": {
            "note": "GRIB2 multi-messages (découper par step). Cumuls depuis début run (soustraction de 2 échéances).",
            "descriptor": "grib_ls -p shortName,paramId (shortNames ci-dessous confirmés)",
            "quality": "",
            "fields": [
                {"name": "tp (SP1)", "type": "GRIB", "unit": "kg/m²", "desc": "cumul précip total"},
                {"name": "tsnowp / tgrp (SP1)", "type": "GRIB", "unit": "kg/m²", "desc": "cumul neige / grésil (grésil servi ici, ≠ WCS)"},
                {"name": "2t / 2d (SP1/SP2)", "type": "GRIB", "unit": "K", "desc": "T 2 m / point de rosée 2 m"},
                {"name": "10u / 10v / max_i10fg", "type": "GRIB", "unit": "m/s", "desc": "vent 10 m / rafale"},
                {"name": "z (IP1)", "type": "GRIB", "unit": "m²/s²", "desc": "géopotentiel (÷9.80665 → m), 24 niveaux 100→1000 hPa"},
                {"name": "t / u / v / r (IP1)", "type": "GRIB", "unit": "K / m·s⁻¹ / %", "desc": "upper-air par niveau isobare"},
            ],
        },
    },
    {
        "id": "AROME-OM",
        "label": "Paquets AROME Outre-mer (DPPaquetAROME-OM)",
        "host": "public-api.meteofrance.fr",
        "context": "/previnum/DPPaquetAROME-OM/v1",
        "url_template": "/previnum/DPPaquetAROME-OM/v1/models/{MODEL}/grids/0.025/packages/{PKG}/{PRODUCT}?referencetime={RUN}&time={001H}&format=grib2",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/previnum/DPPaquetAROME-OM/v1/models/AROME-OM-INDIEN/grids/0.025/packages",
        "notes": [
            "5 territoires, libellés TRONQUÉS + product distinct (ne pas deviner les noms longs → 404) :",
            "INDIEN/productOMOI · ANTIL/productOMAN · GUYANE/productOMGU · NCALED/productOMNC · POLYN/productOMPF.",
            "Échéance SIMPLE (001H, pas de fenêtre 6 h). Horizon 48 h. Orientation N→S (flip). Antilles/Guyane/Polynésie en lon 0-360 (normaliser −360).",
            "Sélection run = découverte : GET Range bytes=0-0 sur time=048H (206 servi / 404 pas encore ; HEAD → 405). Run complet ~H+8-11.",
        ],
        "schema": {
            "note": "GRIB2. Parité variables avec AROME métropole (mêmes shortNames SP1/SP2).",
            "descriptor": "GET .../models/{MODEL}/grids/0.025/packages (paquets servis)",
            "quality": "",
            "fields": [],
        },
    },
    {
        "id": "ARPEGE-Paquets",
        "label": "Modèle ARPEGE — Paquets (DPPaquetARPEGE)",
        "host": "public-api.meteofrance.fr",
        "context": "/previnum/DPPaquetARPEGE/v1",
        "url_template": "/previnum/DPPaquetARPEGE/v1/models/ARPEGE/grids/{0.1|0.25}/packages/{PKG}/productARP?referencetime={RUN}&time={SEGMENT}&format=grib2",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/previnum/DPPaquetARPEGE/v1/models/ARPEGE/grids/0.25/packages",
        "notes": [
            "Suffixe produit `productARP`. Segments 24 h : 000H024H / 025H048H / 049H072H / 073H102H (≠ AROME 6 h !). Runs 00/06/12/18 UTC, ~3 j de rétention.",
            "Piège : `000H006H` passe le regex serveur mais → 404 « La donnée est indisponible ». Découverte : GET .../packages/{PKG}?referencetime={RUN}.",
            "Pas de pipeline actif (ARPEGE Europe servi par Open-Meteo) — référence de contrat.",
        ],
        "schema": {
            "note": "GRIB2. SP1 (P mer, vent 10 m + rafales, T2m, HU2m, nébul, précip, neige, flux), SP2, IP1/IP3/IP4 isobares.",
            "descriptor": "grib_ls",
            "quality": "",
            "fields": [],
        },
    },
    {
        "id": "AROMEPI",
        "label": "AROME-PI — prévision immédiate (nowcast)",
        "host": "public-api.meteofrance.fr",
        "context": "/public/aromepi/{wms|wcs}/{coll}",
        "url_template": "/public/aromepi/{wms|wcs}/MF-NWP-HIGHRES-AROMEPI-{001|0025}-FRANCE-{WMS|WCS}/...",
        "status": "✅ vérifié en live (WCS)",
        "auth": "token",
        "probe": "https://public-api.meteofrance.fr/public/aromepi/wcs/MF-NWP-HIGHRES-AROMEPI-001-FRANCE-WCS/GetCapabilities?service=WCS&version=2.0.1",
        "notes": [
            "Alias /1.0 accepté. Collections 001 (0.01°) et 0025 (0.025°). Même mécanique WCS qu'AROME. Valider par DescribeCoverage.",
        ],
        "schema": {"note": "Coverages nowcast WCS.", "descriptor": "GetCapabilities → DescribeCoverage", "quality": "", "fields": []},
    },
    {
        "id": "PIAF",
        "label": "PIAF — prévision immédiate précip (nowcast, COMMERCIAL)",
        "host": "api.meteofrance.fr",
        "context": "/pro/piaf/1.0/{wms|wcs}/{coll}",
        "url_template": "/pro/piaf/1.0/{wms|wcs}/MF-NWP-HIGHRES-PIAF-001-FRANCE-{WMS|WCS}/{GetCapabilities|DescribeCoverage|GetCoverage}",
        "status": "✅ vérifié en live",
        "auth": "token",
        "probe": "https://api.meteofrance.fr/pro/piaf/1.0/wcs/MF-NWP-HIGHRES-PIAF-001-FRANCE-WCS/GetCapabilities?service=WCS&version=2.0.1",
        "notes": [
            "HOST COMMERCIAL api.meteofrance.fr (PAS public-api). Grille 0.01° uniquement, France métropole.",
            "coverageId TOTAL_PRECIPITATION_RATE__GROUND_OR_WATER_SURFACE___{run}_PT15M. Axe time toutes les 300 s, T+15 min à T+3h15.",
        ],
        "schema": {
            "note": "GRIB2 (~3.5 Mo). Pas de dimension height.",
            "descriptor": "DescribeCoverage : bbox lon −6..10.5 / lat 41..51.5",
            "quality": "",
            "fields": [
                {"name": "TOTAL_PRECIPITATION_RATE", "type": "GRIB", "unit": "kg/m²/s", "desc": "intensité précip fusionnée, pas 15 min"},
            ],
        },
    },
    {
        "id": "climato-data-gouv",
        "label": "Climatologie archives open data (meteo.data.gouv.fr)",
        "host": "object.files.data.gouv.fr",
        "context": "/meteofrance/data/synchro_ftp/BASE/{MN,HOR,QUOT,MENS,DECAD,DECADAGRO}",
        "url_template": "https://object.files.data.gouv.fr/meteofrance/data/synchro_ftp/BASE/{FREQ}/{FREQ}_{DD}_{periode}[_{PARAM}].csv.gz",
        "status": "✅ open data (sans token)",
        "auth": "none",
        "probe": "https://object.files.data.gouv.fr/meteofrance/data/synchro_ftp/BASE/HOR/H_descriptif_champs.csv",
        "notes": [
            "Sans auth (Licence Ouverte Etalab 2.0). Listing des ressources : https://www.data.gouv.fr/api/2/datasets/{DATASET_ID}/resources/.",
            "Ingéré par le repo telechargement-climatologie-meteo-data-gouv → tables timescaledb Horaire/Quotidienne/Mensuelle/Decadaire(Agro)/Infrahoraire.",
            "≠ bucket AROME PNT (object.data.gouv.fr/meteofrance-pnt).",
        ],
        "schema": {
            "note": "Unités CONVENTIONNELLES (°C, 1/10) — pas SI. Une colonne par paramètre + indicateur qualité Q* (ou C* pour DecadaireAgro).",
            "descriptor": "BASE/{FREQ}/{FREQ}_descriptif_champs.csv (ex. H_descriptif_champs.csv). DDL persisté via tool `schema`.",
            "quality": "Q<PARAM> = code qualité par paramètre (C<PARAM> pour DecadaireAgro).",
            "fields": [
                {"name": "NUM_POSTE", "type": "TEXT", "unit": "8 chiffres", "desc": "numéro de poste MF"},
                {"name": "AAAAMMJJHH / AAAAMMJJ / AAAAMM", "type": "TIMESTAMP", "unit": "—", "desc": "clé temporelle selon la fréquence"},
                {"name": "RR / RR1", "type": "REAL", "unit": "mm", "desc": "précipitations (Q : QRR)"},
                {"name": "T / TN / TX", "type": "REAL", "unit": "°C", "desc": "température (moy/min/max)"},
                {"name": "FF / FXY", "type": "REAL", "unit": "m/s", "desc": "vent moyen / rafale"},
            ],
        },
    },
]

_BY_ID = {e["id"].lower(): e for e in CATALOG}


# ── Recherche ────────────────────────────────────────────────────────────────

def find(query: str):
    """Retrouve une entrée par id (insensible à la casse) ou fragment de label/context."""
    q = (query or "").strip().lower()
    if not q:
        return None
    if q in _BY_ID:
        return _BY_ID[q]
    # fragment : id qui contient q, sinon label/context
    for e in CATALOG:
        if q in e["id"].lower():
            return e
    for e in CATALOG:
        if q in e["label"].lower() or q in e["context"].lower():
            return e
    return None


# ── Rendu ────────────────────────────────────────────────────────────────────

def overview() -> str:
    lines = ["# Catalogue des APIs Météo-France\n", AUTH, "", "## Table rapide", ""]
    lines.append("| API | Host | Context | Statut |")
    lines.append("|---|---|---|---|")
    for e in CATALOG:
        lines.append(f"| `{e['id']}` | `{e['host']}` | `{e['context']}` | {e['status']} |")
    lines += ["", "## Taxonomie d'erreurs (interprétation des probes)", ""]
    for code, meaning in ERROR_TAXONOMY.items():
        lines.append(f"- **{code}** : {meaning}")
    lines += [
        "",
        "→ `meteofrance_catalog(api=\"<id>\")` pour le contrat, `topic=\"schema\"` pour les "
        "champs/unités, `probe=true` pour tester la disponibilité.",
    ]
    return "\n".join(lines)


def render_contract(e: dict) -> str:
    lines = [
        f"# {e['label']}  (`{e['id']}`)",
        f"- **Host** : `{e['host']}`",
        f"- **Context** : `{e['context']}`",
        f"- **URL** : `{e['url_template']}`",
        f"- **Statut** : {e['status']}",
        f"- **Auth** : {e['auth']}",
        "",
        "**Quirks / pièges :**",
    ]
    lines += [f"- {n}" for n in e["notes"]]
    return "\n".join(lines)


def render_schema(e: dict) -> str:
    sc = e["schema"]
    lines = [f"# Schéma de données — {e['label']}  (`{e['id']}`)"]
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
        lines += ["", "(Pas de schéma tabulaire pour cette API — cf. descripteur.)"]
    if sc.get("descriptor"):
        lines += ["", f"**Descripteur exhaustif** : {sc['descriptor']}"]
    return "\n".join(lines)


def not_found(query: str) -> str:
    ids = ", ".join(e["id"] for e in CATALOG)
    return (
        f"API Météo-France inconnue : « {query} ». "
        f"APIs au catalogue : {ids}. "
        "Appelle `meteofrance_catalog()` sans argument pour la vue d'ensemble."
    )


# ── Probe (disponibilité) ────────────────────────────────────────────────────

def interpret(status: int, snippet: str) -> str:
    s = snippet or ""
    if status in (200, 206):
        return "✅ servi — l'application est abonnée et l'endpoint répond."
    if status == 401:
        return "🔑 401 — token invalide/expiré (un refresh est nécessaire)."
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
    """Probe la disponibilité de l'entrée via `client.probe(url, auth=...)`."""
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
