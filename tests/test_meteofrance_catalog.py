import pytest

from ic_data_bot.tools import ToolBox, ToolError
from ic_data_bot import meteofrance_catalog as cat


class _StubMF:
    """Client Météo-France factice : renvoie un résultat de probe canné, sans réseau."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def probe(self, url, *, auth=True, range_probe=True):
        self.calls.append((url, auth))
        return self.result


def _box(tmp_path, meteofrance=None):
    return ToolBox(tmp_path, meteofrance=meteofrance)


# ── Vue d'ensemble & contrat ────────────────────────────────────────────────

def test_overview_lists_apis(tmp_path):
    out = _box(tmp_path).meteofrance_catalog()
    assert "DPObs" in out
    assert "AROME" in out
    assert "PIAF" in out
    assert "900908" in out  # taxonomie d'erreurs présente


def test_contract_returns_host_and_context(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="contract")
    assert "public-api.meteofrance.fr" in out
    assert "/public/DPObs/v1" in out


def test_piaf_uses_commercial_host(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="piaf")
    assert "api.meteofrance.fr" in out


def test_fuzzy_match(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="arome-paquets")
    assert "DPPaquetAROME" in out


def test_unknown_api_is_helpful(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="inexistant")
    assert "inconnue" in out.lower()
    assert "DPObs" in out  # liste les apis disponibles


# ── Schéma de données ───────────────────────────────────────────────────────

def test_schema_dpobs_has_fields_and_units(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="schema")
    assert "geo_id_insee" in out
    assert "Kelvin" in out  # unité SI brute signalée
    assert "SI BRUTES" in out


def test_topic_all_combines(tmp_path):
    out = _box(tmp_path).meteofrance_catalog(api="DPObs", topic="all")
    assert "/public/DPObs/v1" in out  # contrat
    assert "geo_id_insee" in out      # schéma


# ── Probe ───────────────────────────────────────────────────────────────────

def test_probe_without_client_raises(tmp_path):
    with pytest.raises(ToolError):
        _box(tmp_path).meteofrance_catalog(api="DPObs", probe=True)


def test_probe_served(tmp_path):
    stub = _StubMF({"status": 200, "content_type": "application/json", "snippet": "[]"})
    out = _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="DPObs", probe=True)
    assert "servi" in out
    assert stub.calls and stub.calls[0][1] is True  # auth bearer demandé


def test_probe_not_subscribed(tmp_path):
    stub = _StubMF({"status": 403, "content_type": "application/json",
                    "snippet": '{"code":"900908","message":"API Subscription validation failed"}'})
    out = _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="DPRadar", probe=True)
    assert "900908" in out
    assert "abonn" in out.lower()


def test_probe_open_data_no_auth(tmp_path):
    stub = _StubMF({"status": 200, "content_type": "text/csv", "snippet": "Mnémonique,..."})
    _box(tmp_path, meteofrance=stub).meteofrance_catalog(api="climato-data-gouv", probe=True)
    assert stub.calls and stub.calls[0][1] is False  # entrée auth=none → pas de bearer


# ── Interprétation des statuts ──────────────────────────────────────────────

def test_interpret_taxonomy():
    assert "servi" in cat.interpret(206, "")
    assert "404" in cat.interpret(404, "No matching resource")
    assert "NoSuchCoverage" in cat.interpret(404, "NoSuchCoverage")
    assert "EXISTE" in cat.interpret(400, "")


# ── Dispatch (caviardage + intégration) ─────────────────────────────────────

def test_dispatch_meteofrance_catalog(tmp_path):
    out = ToolBox(tmp_path).dispatch("meteofrance_catalog", {"api": "DPObs", "topic": "schema"})
    assert "geo_id_insee" in out
