from ic_data_bot.context import SYSTEM_PERSONA, summarize_registry, build_system_blocks


def _make_snapshot(tmp_path):
    (tmp_path / "README.md").write_text("Plateforme data Infoclimat\n")
    cat = tmp_path / "catalog"; cat.mkdir()
    cat.joinpath("catalog.yaml").write_text("datasets:\n  - id: foudre\n  - id: climato\n")
    cat.joinpath("glossary.md").write_text("ODCS: Open Data Contract Standard\n")
    con = tmp_path / "contracts"; con.mkdir()
    con.joinpath("foudre.odcs.yaml").write_text("id: foudre\n")
    con.joinpath("_template.odcs.yaml").write_text("id: template\n")
    inv = tmp_path / "inventory"; inv.mkdir()
    inv.joinpath("README.md").write_text("registres vivants\n")
    inv.joinpath("tables.yaml").write_text("- name: a\n- name: b\n- name: c\n")
    return tmp_path


def test_summarize_registry_counts_list(tmp_path):
    f = tmp_path / "tables.yaml"
    f.write_text("- name: a\n- name: b\n")
    s = summarize_registry(f)
    assert "2 entrées" in s


def test_build_system_blocks_structure(tmp_path):
    root = _make_snapshot(tmp_path)
    blocks = build_system_blocks(root)
    assert blocks[0]["text"] == SYSTEM_PERSONA
    # le dernier bloc porte le cache_control
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    joined = "\n".join(b["text"] for b in blocks)
    assert "Plateforme data Infoclimat" in joined
    assert "ODCS" in joined
    assert "foudre.odcs.yaml" in joined        # contrat inclus
    assert "_template" not in joined           # template exclu
    assert "tables.yaml" in joined and "3 entrées" in joined  # résumé registre


def test_format_contracts_message(tmp_path):
    from ic_data_bot.context import format_contracts_message

    con = tmp_path / "contracts"; con.mkdir()
    con.joinpath("foudre.odcs.yaml").write_text(
        "name: Données foudre\nstatus: draft\ndomain: foudre\n"
    )
    con.joinpath("synop.odcs.yaml").write_text(
        "name: Synop\nstatus: active\ndomain: observations\n"
    )
    con.joinpath("_template.odcs.yaml").write_text("name: tpl\n")

    msg = format_contracts_message(tmp_path)
    assert "Contrats ODCS (2)" in msg
    assert "✅ `synop.odcs.yaml` — Synop (active, observations)" in msg
    assert "📋 `foudre.odcs.yaml` — Données foudre (draft, foudre)" in msg
    assert "_template" not in msg


def test_format_contracts_message_empty(tmp_path):
    from ic_data_bot.context import format_contracts_message
    assert "Aucun dossier" in format_contracts_message(tmp_path)


def test_summarize_catalog_skeleton(tmp_path):
    from ic_data_bot.context import summarize_catalog
    f = tmp_path / "catalog.yaml"
    f.write_text(
        "datasets:\n"
        "  - id: foudre\n    name: Foudre\n    status: active\n"
        "    storage:\n      - system: mariadb\n      - system: timescaledb\n"
        "    contract: contracts/foudre.odcs.yaml\n"
        "  - id: radar\n    name: Radar\n    status: draft\n"
    )
    s = summarize_catalog(f)
    assert "2 datasets" in s
    assert "- foudre (Foudre) [active] — mariadb, timescaledb — contracts/foudre.odcs.yaml" in s
    assert "- radar (Radar) [draft]" in s


def test_summarize_contract_compact_drops_columns(tmp_path):
    from ic_data_bot.context import summarize_contract
    f = tmp_path / "c.odcs.yaml"
    f.write_text(
        "name: Foudre\nstatus: active\ndomain: foudre\n"
        "description:\n  purpose: Impacts de foudre.\n"
        "schema:\n"
        "  - name: foudre\n    properties:\n"
        "      - name: dh_usec\n        customProperties:\n          - property: unit\n            value: ms\n"
        "      - name: lat\n"
    )
    compact = summarize_contract(f, compact=True)
    full = summarize_contract(f, compact=False)
    assert "tables : foudre" in compact          # noms de tables seulement
    assert "dh_usec" not in compact              # pas de colonnes en compact
    assert "dh_usec (ms)" in full                # détail (unité) conservé en non-compact
