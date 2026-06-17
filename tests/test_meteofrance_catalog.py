import textwrap
import pytest

from ic_data_bot.tools import ToolBox, ToolError
from ic_data_bot import meteofrance_catalog as cat


def _write(root, fname, body):
    d = root / "contracts"
    d.mkdir(exist_ok=True)
    (d / fname).write_text(textwrap.dedent(body), encoding="utf-8")


def _seed(root):
    """Sème des contrats ODCS de source réalistes mais minimaux dans root/contracts/."""
    _write(root, "source-meteofrance-dpobs.odcs.yaml", """
        apiVersion: v3.0.2
        kind: DataContract
        id: urn:infoclimat:contract:source-meteofrance-dpobs
        name: Source Météo-France — Observations stations (DPObs)
        version: 2.0.0
        status: active
        domain: observations
        tags: [source, meteofrance, api]
        schema:
          - name: station-observation
            physicalType: object
            properties:
              - name: geo_id_insee
                physicalType: TEXT
                unit: ddnnnpp
                description: ID point
              - name: ff
                physicalType: REAL
                unit: m/s
                description: vent moyen à 10 m
              - name: t
                physicalType: REAL
                unit: K (Kelvin)
                description: température sous abri (SI brut)
        customProperties:
          - property: externalSource
            value:
              apiId: DPObs
              host: public-api.meteofrance.fr
              context: /public/DPObs/v1
              urlTemplate: /public/DPObs/v1/station/horaire?id_station={id}&format=json
              auth: token
              probeUrl: https://public-api.meteofrance.fr/public/DPObs/v1/liste-stations
              verified: "verifie en live"
              unitsNote: "Unites SI BRUTES dans la reponse API : temperature en KELVIN, pression en PASCAL."
              quirks:
                - Stations RADOME, 24 dernieres heures.
          - property: changelog
            value:
              - version: "2.0.0"
                date: "2026-05-12"
                type: rename
                severity: breaking
                fields: [ff, fxi10]
                note: "Passage v2 vent : ff renomme, rafale fxi10 -> fxi."
              - version: "1.0.0"
                date: "2026-06-17"
                type: initial
                severity: non-breaking
                fields: []
                note: "Transcription du dict (etat v1)."
    """)
    _write(root, "source-meteofrance-climato.odcs.yaml", """
        apiVersion: v3.0.2
        kind: DataContract
        id: urn:infoclimat:contract:source-meteofrance-climato-data-gouv
        name: Source Météo-France — Climatologie data.gouv
        version: 1.0.0
        status: active
        domain: climato
        tags: [source, meteofrance]
        schema:
          - name: poste-horaire
            physicalType: object
            properties:
              - name: T
                physicalType: REAL
                unit: "°C"
                description: température (unité conventionnelle)
        customProperties:
          - property: externalSource
            value:
              apiId: climato-data-gouv
              host: object.files.data.gouv.fr
              context: /meteofrance/data/synchro_ftp/BASE
              urlTemplate: https://object.files.data.gouv.fr/.../{FREQ}.csv.gz
              auth: none
              probeUrl: https://object.files.data.gouv.fr/meteofrance/data/synchro_ftp/BASE/HOR/H_descriptif_champs.csv
              verified: open data
              unitsNote: "Unites CONVENTIONNELLES (°C, 1/10)."
              quirks: [Sans auth - Licence Ouverte Etalab 2.0.]
          - property: changelog
            value: []
    """)
    _write(root, "source-meteofrance-piaf.odcs.yaml", """
        apiVersion: v3.0.2
        kind: DataContract
        id: urn:infoclimat:contract:source-meteofrance-piaf
        name: Source Météo-France — PIAF (nowcast precip, commercial)
        version: 1.0.0
        status: active
        domain: modeles
        tags: [source, meteofrance, api]
        schema:
          - name: piaf
            physicalType: object
            properties:
              - name: TOTAL_PRECIPITATION_RATE
                physicalType: GRIB
                unit: kg/m²/s
                description: intensité précip fusionnée
        customProperties:
          - property: externalSource
            value:
              apiId: PIAF
              host: api.meteofrance.fr
              context: /pro/piaf/1.0/wcs
              urlTemplate: /pro/piaf/1.0/wcs/.../GetCoverage
              auth: token
              probeUrl: https://api.meteofrance.fr/pro/piaf/1.0/wcs/x/GetCapabilities
              verified: verifie en live
              quirks: [HOST COMMERCIAL api.meteofrance.fr.]
          - property: changelog
            value: []
    """)
    # Contrat non-source (table persistée) : la vue doit l'IGNORER.
    _write(root, "climato-mf-timescale.odcs.yaml", """
        apiVersion: v3.0.2
        kind: DataContract
        id: urn:infoclimat:contract:climato-mf-timescale
        name: Climato MF persistée
        version: 0.1.0
        status: active
        tags: [timescaledb]
        customProperties: []
    """)


def _box(root, meteofrance=None):
    _seed(root)
    return ToolBox(root, meteofrance=meteofrance)


class _StubMF:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def probe(self, url, *, auth=True, range_probe=True):
        self.calls.append((url, auth))
        return self.result


# ── Vue d'ensemble & contrat ────────────────────────────────────────────────

def test_overview_lists_only_source_contracts(tmp_path):
    out = _box(tmp_path).meteofrance_catalog()
    assert "DPObs" in out
    assert "PIAF" in out
    assert "900908" in out
    assert "persistée" not in out


def test_contract_returns_host_and_context(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="contract")
    assert "public-api.meteofrance.fr" in out
    assert "/public/DPObs/v1" in out


def test_piaf_uses_commercial_host(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="piaf")
    assert "api.meteofrance.fr" in out


def test_fuzzy_match_by_context(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="climato")
    assert "data.gouv" in out


def test_unknown_api_is_helpful(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="inexistant")
    assert "inconnue" in out.lower()
    assert "DPObs" in out


# ── Schéma de données ───────────────────────────────────────────────────────

def test_schema_dpobs_has_fields_and_units(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="schema")
    assert "geo_id_insee" in out
    assert "Kelvin" in out
    assert "SI BRUTES" in out


# ── Historique (NOUVEAU) ────────────────────────────────────────────────────

def test_changes_lists_versions_breaking(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="changes")
    assert "2.0.0" in out
    assert "breaking" in out.lower()
    assert "ff" in out


def test_changes_empty_is_explicit(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="PIAF", topic="changes")
    assert "aucun changement" in out.lower()


def test_changes_since_filters(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="changes", since="2026-01-01")
    assert "2.0.0" in out
    out2 = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="changes", since="2026-06-01")
    assert "2.0.0" not in out2


# ── Probe ───────────────────────────────────────────────────────────────────

def test_probe_without_client_raises(tmp_path):
    with pytest.raises(ToolError):
        _box(tmp_path).meteofrance_catalog(api="DPObs", probe=True)


def test_probe_served(tmp_path):
    stub = _StubMF({"status": 200, "content_type": "application/json", "snippet": "[]"})
    out = _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="DPObs", probe=True)
    assert "servi" in out
    assert stub.calls and stub.calls[0][1] is True


def test_probe_not_subscribed(tmp_path):
    stub = _StubMF({"status": 403, "content_type": "application/json",
                    "snippet": '{"code":"900908","message":"API Subscription validation failed"}'})
    out = _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="DPObs", probe=True)
    assert "900908" in out
    assert "abonn" in out.lower()


def test_probe_open_data_no_auth(tmp_path):
    stub = _StubMF({"status": 200, "content_type": "text/csv", "snippet": "x"})
    _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="climato-data-gouv", probe=True)
    assert stub.calls and stub.calls[0][1] is False


# ── Interprétation (constante, inchangée) ───────────────────────────────────

def test_interpret_taxonomy():
    assert "servi" in cat.interpret(206, "")
    assert "NoSuchCoverage" in cat.interpret(404, "NoSuchCoverage")
    assert "EXISTE" in cat.interpret(400, "")


def test_dispatch_meteofrance_catalog(tmp_path):
    _seed(tmp_path)
    out = ToolBox(tmp_path).dispatch("meteofrance_catalog", {"api": "DPObs", "topic": "schema"})
    assert "geo_id_insee" in out


def test_dispatch_changes_with_since(tmp_path):
    _seed(tmp_path)
    box = ToolBox(tmp_path)
    out = box.dispatch("meteofrance_catalog",
                       {"api": "DPObs", "topic": "changes", "since": "2026-06-01"})
    # le changement breaking du 2026-05-12 doit être filtré par since=2026-06-01
    assert "2.0.0" not in out
    out_all = box.dispatch("meteofrance_catalog", {"api": "DPObs", "topic": "changes"})
    assert "2.0.0" in out_all
