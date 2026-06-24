import pytest

from ic_data_bot.tools import ToolBox, ToolError


def _snapshot(tmp_path):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "foo.odcs.yaml").write_text("name: foo\nstatus: active\n", encoding="utf-8")
    (tmp_path / "_ops").mkdir()
    # overlay confidentiel : liste YAML avec une valeur interne unique
    (tmp_path / "_ops" / "ops-mapping.yaml").write_text(
        "ops_systems:\n  - name: secretthing\n    ip: 203.0.113.250\n", encoding="utf-8")
    return tmp_path


def test_public_read_file_refuses_ops(tmp_path):
    tb = ToolBox(_snapshot(tmp_path), public=True)
    assert "name: foo" in tb.read_file("contracts/foo.odcs.yaml")
    with pytest.raises(ToolError):
        tb.read_file("_ops/ops-mapping.yaml")


def test_public_grep_skips_ops(tmp_path):
    tb = ToolBox(_snapshot(tmp_path), public=True)
    out = tb.grep("203.0.113.250")
    assert "203.0.113.250" not in out  # l'overlay _ops est ignoré


def test_public_lineage_excludes_ops(tmp_path):
    tb = ToolBox(_snapshot(tmp_path), public=True)
    out = tb.lineage("secretthing")
    assert "203.0.113.250" not in out


def test_private_mode_still_serves_ops(tmp_path):
    """Le bot (public=False, défaut) garde l'accès à l'overlay — comportement inchangé."""
    tb = ToolBox(_snapshot(tmp_path), public=False)
    assert "203.0.113.250" in tb.read_file("_ops/ops-mapping.yaml")
    assert "203.0.113.250" in tb.lineage("secretthing")


def test_mcp_server_imports_and_builds_app():
    import os
    os.environ["MCP_BEARER_TOKEN"] = "test-token"
    from ic_data_bot import mcp_server
    app = mcp_server._build_app()
    assert app is not None


def test_graph_tools_gated_like_code_impact(monkeypatch):
    """data_to_code/code_path/dead_code partagent la grille de code_impact
    (GRAPH_INDEX_ENABLED ET CODE_INDEX_PUBLIC) : enregistrés ssi les deux flags sont posés."""
    import asyncio
    import importlib

    from ic_data_bot import mcp_server

    def _registered(enable_graph: bool):
        monkeypatch.setenv("GRAPH_INDEX_ENABLED", "1" if enable_graph else "0")
        monkeypatch.setenv("CODE_INDEX_PUBLIC", "1")
        mod = importlib.reload(mcp_server)
        names = {t.name for t in asyncio.run(mod.mcp.list_tools())}
        return names

    on = _registered(True)
    assert {"data_to_code", "code_path", "dead_code"} <= on
    assert "code_impact" in on  # même grille
    off = _registered(False)
    assert not ({"data_to_code", "code_path", "dead_code"} & off)
    assert "code_impact" not in off
    # restaure le module dans son état par défaut pour les autres tests
    monkeypatch.delenv("GRAPH_INDEX_ENABLED", raising=False)
    monkeypatch.delenv("CODE_INDEX_PUBLIC", raising=False)
    importlib.reload(mcp_server)


def test_valid_tokens_multi(monkeypatch):
    from ic_data_bot import mcp_server
    monkeypatch.setenv("MCP_BEARER_TOKEN", "main")
    monkeypatch.setenv("MCP_BEARER_TOKENS", "alice, bob ,")
    assert mcp_server._valid_tokens() == {"main", "alice", "bob"}
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("MCP_BEARER_TOKENS", "only")
    assert mcp_server._valid_tokens() == {"only"}
