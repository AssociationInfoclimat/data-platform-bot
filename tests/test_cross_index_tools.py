"""Outils croisés registre × graphe d'appels (data_to_code, code_path, dead_code) et
filtres status/source/since sur search_code/search_docs.

Hermétiques : pour la moitié « graphe » on monkeypatche ToolBox._load_graph pour renvoyer
un faux module graphe (stub) exposant uniquement les fonctions appelées, avec des dicts
canoniques qui RESPECTENT les formes de retour des primitives de data-platform/tools/
code_index/graph.py (shortest_path / dead_symbols / resolve_file / code_impact). On teste
NOTRE formatage, pas l'algorithme (déjà testé côté data-platform)."""

import types

import pytest

from ic_data_bot.tools import ToolBox, ToolError, SCHEMAS


# ── Stub graphe : un faux module code_index.graph minimal ────────────────────
def _node(qname, repo="site-infoclimat", path="api/x.php", start=10, end=20,
          confidence=1.0, **extra):
    d = {"qname": qname, "kind": "function", "repo": repo, "path": path, "lang": "php",
         "start_line": start, "end_line": end, "depth": 0,
         "source_url": f"https://github.com/x/{repo}/blob/abc/{path}#L{start}-L{end}",
         "confidence": confidence, "tier": "certain", "subsystem": f"{repo}/api",
         "centrality": 0.1, "fan_in": 3}
    d.update(extra)
    return d


def _stub_graph(**fns):
    """Module factice n'exposant QUE les fonctions passées (canned dicts)."""
    mod = types.SimpleNamespace(**fns)
    return mod


def _patch_graph(box, monkeypatch, **fns):
    mod = _stub_graph(**fns)
    monkeypatch.setattr(box, "_load_graph", lambda: (mod, {"nodes": {}}, None))
    return mod


# ── Deliverable 2 : code_path ────────────────────────────────────────────────
def test_code_path_found_renders_chain(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"found": True, "min_confidence": 0.7, "direction": "src->dst",
           "src_roots": ["a"], "dst_roots": ["c"],
           "path": [_node("A", path="api/a.php", confidence=1.0),
                    _node("B", path="api/b.php", confidence=0.9),
                    _node("C", path="api/c.php", confidence=0.7)]}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.code_path("A", "C")
    assert "A" in out and "B" in out and "C" in out
    assert "→" in out  # chaîne
    assert "api/a.php:10" in out and "api/c.php:10" in out
    assert "0.9" in out or "0.90" in out  # confiance d'un hop


def test_code_path_reverse_direction_noted(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"found": True, "min_confidence": 0.5, "direction": "dst->src",
           "src_roots": ["a"], "dst_roots": ["c"],
           "path": [_node("A"), _node("C")]}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.code_path("A", "C")
    assert "dst" in out.lower() or "inverse" in out.lower() or "sens" in out.lower()


def test_code_path_not_found(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"found": False, "min_confidence": 0.0, "direction": None,
           "src_roots": ["a"], "dst_roots": ["c"], "path": []}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.code_path("A", "C")
    assert "aucun" in out.lower() or "pas de" in out.lower()


def test_code_path_unknown_endpoint(tmp_path, monkeypatch):
    """src ou dst absent du graphe : src_roots/dst_roots vides → on le signale."""
    box = ToolBox(tmp_path, public=False)
    res = {"found": False, "min_confidence": 0.0, "direction": None,
           "src_roots": [], "dst_roots": ["c"], "path": []}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.code_path("Inconnu", "C")
    assert "Inconnu" in out


def test_code_path_ambiguous_roots(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"found": True, "min_confidence": 1.0, "direction": "src->dst",
           "src_roots": ["a1", "a2"], "dst_roots": ["c"],
           "path": [_node("A"), _node("C")]}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.code_path("A", "C")
    assert "2" in out  # plusieurs racines src signalées


def test_code_path_refused_in_public_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_INDEX_PUBLIC", raising=False)
    box = ToolBox(tmp_path, public=True)
    with pytest.raises(ToolError):
        box.code_path("A", "C")


def test_code_path_empty_args(tmp_path):
    box = ToolBox(tmp_path, public=False)
    with pytest.raises(ToolError):
        box.code_path("  ", "C")


# ── Deliverable 3 : dead_code ────────────────────────────────────────────────
def test_dead_code_renders_caveat_and_list(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"count": 2, "truncated": True, "caveat": "attention : les points d'entrée…",
           "symbols": [_node("dead_a", path="api/a.php", start=5),
                       _node("dead_b", path="cron/b.php", start=8)]}
    _patch_graph(box, monkeypatch, dead_symbols=lambda g, **k: res)
    out = box.dead_code()
    assert "attention" in out.lower()  # caveat d'abord
    assert "api/a.php:5" in out and "cron/b.php:8" in out
    assert "tronqué" in out.lower() or "truncated" in out.lower() or "tronquée" in out.lower()


def test_dead_code_empty(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"count": 0, "truncated": False, "caveat": "attention …", "symbols": []}
    _patch_graph(box, monkeypatch, dead_symbols=lambda g, **k: res)
    out = box.dead_code(repo="inexistant")
    assert "aucun" in out.lower()


def test_dead_code_refused_in_public_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_INDEX_PUBLIC", raising=False)
    box = ToolBox(tmp_path, public=True)
    with pytest.raises(ToolError):
        box.dead_code()


# ── Deliverable 1 : data_to_code ─────────────────────────────────────────────
def _registry_snapshot(tmp_path):
    inv = tmp_path / "inventory"; inv.mkdir()
    (inv / "tables.yaml").write_text(
        "version: 1\ntables:\n"
        "  - name: \"mariadb://V5/foudre\"\n"
        "    writers: ['site-infoclimat:cron/recup_blitzortung.php']\n"
        "    readers: ['site-infoclimat:include/Foudre/carte.php']\n"
        "    status: actif\n"
        "  - name: \"mariadb://V5/autre\"\n    writers: []\n",
        encoding="utf-8")
    (inv / "pipelines.yaml").write_text(
        "version: 1\npipelines:\n"
        "  - id: cron.recup-foudre\n    repo: site-infoclimat\n"
        "    script: cron/recup_foudre.php\n"
        "    outputs: [\"mariadb://V5/foudre\"]\n"
        "    trigger:\n      type: cron\n      source: \"infrapilot:crons/foudre.cron:12\"\n",
        encoding="utf-8")
    con = tmp_path / "contracts"; con.mkdir()
    (con / "foudre.odcs.yaml").write_text(
        "name: Foudre\n"
        "customProperties:\n"
        "  - property: sourceRepo\n    value: \"site-infoclimat:cron/recup_blitzortung.php\"\n"
        "  - property: retroCompatLayer\n    value: \"site-infoclimat:include/Foudre/carte.php\"\n",
        encoding="utf-8")
    return tmp_path


def test_data_to_code_no_match_raises(tmp_path):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    with pytest.raises(ToolError):
        box.data_to_code("inexistant_xyz")


def test_registry_code_refs_extracts_roles(tmp_path):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    refs = box._registry_code_refs("foudre")
    pairs = {(r[0], r[1], r[2]) for r in refs}  # (role, repo, path)
    assert ("writer", "site-infoclimat", "cron/recup_blitzortung.php") in pairs
    assert ("reader", "site-infoclimat", "include/Foudre/carte.php") in pairs
    assert ("pipeline", "site-infoclimat", "cron/recup_foudre.php") in pairs
    assert ("source", "infrapilot", "crons/foudre.cron") in pairs  # ligne :12 strippée
    assert ("retrocompat", "site-infoclimat", "include/Foudre/carte.php") in pairs


def test_data_to_code_enriches_with_graph(tmp_path, monkeypatch):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    impact = {"roots": [_node("seed")], "impacted": [_node("CallerX", path="api/c.php")],
              "scope": "file", "files": ["site-infoclimat/cron/recup_blitzortung.php"],
              "ambiguous": False, "truncated": False, "by_subsystem": {}, "tiers": {}}
    seen_syms = []

    def _capt_impact(g, sym, **k):
        seen_syms.append(sym)
        return impact

    _patch_graph(box, monkeypatch,
                 resolve_file=lambda g, p: ["seed"] if "recup_blitzortung" in p else [],
                 code_impact=_capt_impact)
    out = box.data_to_code("foudre")
    assert "writer" in out.lower() or "écrivain" in out.lower() or "Writer" in out
    assert "recup_blitzortung.php" in out
    assert "CallerX" in out  # appelant impacté
    # Régression : code_impact doit recevoir le path REPO-RELATIF (resolve_file matche par
    # endswith) et JAMAIS « repo/path » (sinon 0 racine, impact faussé à zéro).
    assert "cron/recup_blitzortung.php" in seen_syms
    assert not any(s.startswith("site-infoclimat/") for s in seen_syms)


def test_data_to_code_graph_unavailable_still_lists_refs(tmp_path, monkeypatch):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    def _boom():
        raise ToolError("graphe non déployé")
    monkeypatch.setattr(box, "_load_graph", _boom)
    out = box.data_to_code("foudre")
    assert "recup_blitzortung.php" in out  # refs registre listées malgré l'absence de graphe
    assert "indisponible" in out.lower() or "non déployé" in out.lower() or "hors graphe" in out.lower()


def test_data_to_code_ref_not_in_graph_marked(tmp_path, monkeypatch):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    impact = {"roots": [], "impacted": [], "scope": "symbol", "files": [],
              "ambiguous": False, "truncated": False, "by_subsystem": {}, "tiers": {}}
    _patch_graph(box, monkeypatch,
                 resolve_file=lambda g, p: [],  # rien dans le graphe
                 code_impact=lambda g, sym, **k: impact)
    out = box.data_to_code("foudre")
    assert "hors graphe" in out.lower()


def test_data_to_code_refused_in_public_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_INDEX_PUBLIC", raising=False)
    box = ToolBox(_registry_snapshot(tmp_path), public=True)
    with pytest.raises(ToolError):
        box.data_to_code("foudre")


# ── Deliverable 4 : filtres search_code/search_docs ──────────────────────────
def test_search_code_threads_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_ENABLED", "1")
    captured = {}

    def _fake_search(query, k=6, repos=None, status=None, source=None, since=None):
        captured.update(status=status, source=source, since=since, repos=repos)
        return []

    import sys
    fake_mod = types.ModuleType("code_index")
    fake_mod.search_code = _fake_search
    fake_mod.search_docs = _fake_search
    monkeypatch.setitem(sys.modules, "code_index", fake_mod)
    monkeypatch.setattr("ic_data_bot.tools.mistral_throttle", lambda: None)
    box = ToolBox(tmp_path, public=False)
    box.search_code("test", status="actif", source="github", since="2026-01-01")
    assert captured["status"] == "actif"
    assert captured["source"] == "github"
    assert captured["since"] == "2026-01-01"


def test_search_docs_threads_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_INDEX_ENABLED", "1")
    captured = {}

    def _fake_search(query, k=8, status=None, source=None, since=None):
        captured.update(status=status, source=source, since=since)
        return []

    import sys
    fake_mod = types.ModuleType("code_index")
    fake_mod.search_code = _fake_search
    fake_mod.search_docs = _fake_search
    monkeypatch.setitem(sys.modules, "code_index", fake_mod)
    monkeypatch.setattr("ic_data_bot.tools.mistral_throttle", lambda: None)
    box = ToolBox(tmp_path, public=False)
    box.search_docs("test", status="mort", source="gitlab", since="2025-01-01")
    assert captured["status"] == "mort"
    assert captured["source"] == "gitlab"
    assert captured["since"] == "2025-01-01"


def test_search_code_no_filters_when_none(tmp_path, monkeypatch):
    """Comportement inchangé quand status/source/since tous None."""
    monkeypatch.setenv("CODE_INDEX_ENABLED", "1")
    captured = {}

    def _fake_search(query, k=6, repos=None, status=None, source=None, since=None):
        captured.update(status=status, source=source, since=since)
        return []

    import sys
    fake_mod = types.ModuleType("code_index")
    fake_mod.search_code = _fake_search
    fake_mod.search_docs = _fake_search
    monkeypatch.setitem(sys.modules, "code_index", fake_mod)
    monkeypatch.setattr("ic_data_bot.tools.mistral_throttle", lambda: None)
    box = ToolBox(tmp_path, public=False)
    box.search_code("test")
    assert captured == {"status": None, "source": None, "since": None}


# ── Wiring : SCHEMAS + _dispatch ─────────────────────────────────────────────
def test_schemas_include_new_tools():
    names = {t["name"] for t in SCHEMAS}
    assert {"data_to_code", "code_path", "dead_code"} <= names


def test_schemas_search_document_new_filters():
    by_name = {t["name"]: t for t in SCHEMAS}
    for n in ("search_code", "search_docs"):
        props = by_name[n]["input_schema"]["properties"]
        assert "status" in props and "source" in props and "since" in props


def test_dispatch_routes_code_path(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"found": True, "min_confidence": 1.0, "direction": "src->dst",
           "src_roots": ["a"], "dst_roots": ["c"], "path": [_node("A"), _node("C")]}
    _patch_graph(box, monkeypatch, shortest_path=lambda g, s, d, **k: res)
    out = box.dispatch("code_path", {"source": "A", "target": "C"})
    assert "A" in out and "C" in out


def test_dispatch_routes_dead_code(tmp_path, monkeypatch):
    box = ToolBox(tmp_path, public=False)
    res = {"count": 1, "truncated": False, "caveat": "attention …",
           "symbols": [_node("dead_a")]}
    _patch_graph(box, monkeypatch, dead_symbols=lambda g, **k: res)
    out = box.dispatch("dead_code", {"top": 5})
    assert "dead_a" in out


def test_dispatch_routes_data_to_code(tmp_path, monkeypatch):
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    def _boom():
        raise ToolError("graphe non déployé")
    monkeypatch.setattr(box, "_load_graph", _boom)
    out = box.dispatch("data_to_code", {"name": "foudre"})
    assert "recup_blitzortung.php" in out


def test_dispatch_search_code_reads_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_ENABLED", "1")
    captured = {}

    def _fake_search(query, k=6, repos=None, status=None, source=None, since=None):
        captured.update(status=status, source=source, since=since)
        return []

    import sys
    fake_mod = types.ModuleType("code_index")
    fake_mod.search_code = _fake_search
    fake_mod.search_docs = _fake_search
    monkeypatch.setitem(sys.modules, "code_index", fake_mod)
    monkeypatch.setattr("ic_data_bot.tools.mistral_throttle", lambda: None)
    box = ToolBox(tmp_path, public=False)
    box.dispatch("search_code", {"query": "x", "status": "actif", "since": "2026-01-01"})
    assert captured["status"] == "actif" and captured["since"] == "2026-01-01"


def test_graph_tools_skip_llm_scrubber(tmp_path, monkeypatch):
    """Régression : TOUS les outils de code à sortie métadonnées (data_to_code, code_path,
    dead_code, code_impact, code_hotspots) ne passent JAMAIS par le scrubber LLM — celui-ci
    hallucine du contenu de fichier quand la sortie est menée par un chemin. Seul le regex
    redact_secrets s'applique. (search_code/search_docs, eux, GARDENT le scrubber : code brut.)"""
    box = ToolBox(_registry_snapshot(tmp_path), public=False)
    calls = []
    box.secret_scrub = lambda s: calls.append(s) or "SCRUBBED-LLM"
    impact = {"roots": [_node("seed")], "impacted": [], "scope": "file", "files": [],
              "ambiguous": False, "truncated": False, "by_subsystem": {}, "tiers": {}}
    _patch_graph(
        box, monkeypatch,
        resolve_file=lambda g, p: ["seed"],
        code_impact=lambda g, sym, **k: impact,
        code_hotspots=lambda g, **k: {"hotspots": [_node("hub", metric=0.9)], "by": "centrality"},
        shortest_path=lambda g, s, t, **k: {
            "found": True, "path": [_node("a"), _node("b")], "min_confidence": 0.8,
            "src_roots": ["a"], "dst_roots": ["b"], "direction": "src->dst"},
        dead_symbols=lambda g, **k: {
            "count": 1, "truncated": False, "symbols": [_node("d")], "caveat": "x"},
    )
    for out in (box.data_to_code("foudre"), box.code_path("a", "b"), box.dead_code(),
                box.code_impact("seed"), box.code_hotspots()):
        assert "SCRUBBED-LLM" not in out
    assert calls == []  # scrubber LLM jamais invoqué par ces outils


def test_search_tools_still_use_llm_scrubber(monkeypatch):
    """Garde-fou inverse : search_code/search_docs DOIVENT garder le scrubber LLM (ils
    renvoient du code source brut avec des secrets en dur). On le vérifie au niveau source."""
    import inspect
    from ic_data_bot.tools import ToolBox
    for meth in (ToolBox.search_code, ToolBox.search_docs):
        assert "self.secret_scrub" in inspect.getsource(meth)
    for meth in (ToolBox.code_impact, ToolBox.code_hotspots, ToolBox.code_path,
                 ToolBox.dead_code, ToolBox.data_to_code):
        assert "self.secret_scrub" not in inspect.getsource(meth)
