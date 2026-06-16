import pytest
from ic_data_bot.tools import ToolBox, ToolError, SCHEMAS

@pytest.fixture
def snapshot(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "catalog.yaml").write_text("datasets:\n  - id: foudre\n")
    (tmp_path / "README.md").write_text("plateforme data\n")
    return tmp_path

def test_read_file_ok(snapshot):
    box = ToolBox(snapshot, max_bytes=1000)
    assert "plateforme data" in box.read_file("README.md")

def test_read_file_rejects_traversal(snapshot):
    box = ToolBox(snapshot, max_bytes=1000)
    with pytest.raises(ToolError):
        box.read_file("../secret.txt")

def test_read_file_rejects_absolute(snapshot):
    box = ToolBox(snapshot, max_bytes=1000)
    with pytest.raises(ToolError):
        box.read_file("/etc/passwd")

def test_read_file_truncates(snapshot):
    (snapshot / "big.txt").write_text("x" * 5000)
    box = ToolBox(snapshot, max_bytes=100)
    out = box.read_file("big.txt")
    assert len(out) < 5000
    assert "tronqué" in out

def test_grep_finds_matches(snapshot):
    box = ToolBox(snapshot, max_bytes=10000)
    out = box.grep("foudre")
    assert "catalog/catalog.yaml" in out

def test_dispatch_unknown_tool_raises(snapshot):
    box = ToolBox(snapshot, max_bytes=1000)
    with pytest.raises(ToolError):
        box.dispatch("delete_all", {})

def test_schemas_shape():
    names = {t["name"] for t in SCHEMAS}
    assert names == {"read_file", "grep", "lineage", "kestra_recent", "volumetrie", "schema"}


def _lineage_snapshot(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "catalog.yaml").write_text(
        "version: 1\ndatasets:\n"
        "  - id: foudre\n    name: Données foudre\n    storage:\n"
        "      - system: mariadb\n        tables: [foudre]\n"
        "    upstream: [cron.recup-blitzortung]\n"
        "  - id: radar\n    name: Radar\n    storage: []\n"
    )
    inv = tmp_path / "inventory"; inv.mkdir()
    (inv / "tables.yaml").write_text(
        "version: 1\ntables:\n"
        "  - name: mariadb://V5/foudre\n    writers: ['site:cron/recup_blitzortung.php']\n"
        "    readers: ['site:cron/notif_foudre.php', 'site:include/Foudre/carte.php']\n"
        "    status: actif\n"
        "  - name: mariadb://V5/autre\n    writers: []\n"
    )
    (inv / "pipelines.yaml").write_text(
        "version: 1\npipelines:\n"
        "  - id: cron.recup-blitzortung\n    outputs: [mariadb://V5/foudre]\n    status: mort\n"
        "  - id: cron.autre\n    outputs: [x]\n"
    )
    lin = tmp_path / "lineage"; lin.mkdir()
    (lin / "jobs.yaml").write_text(
        "version: 1\njobs:\n"
        "  - pipeline: cron.recup-foudre\n    job_name: recup-foudre\n"
        "  - pipeline: cron.sans-rapport\n    job_name: sans-rapport\n"
    )
    con = tmp_path / "contracts"; con.mkdir()
    (con / "foudre.odcs.yaml").write_text("name: Foudre\nschema:\n  - name: foudre\n")
    (con / "autre.odcs.yaml").write_text("name: Autre\n")
    return tmp_path


def test_lineage_joins_registries(tmp_path):
    from ic_data_bot.tools import ToolBox

    tb = ToolBox(_lineage_snapshot(tmp_path))
    out = tb.lineage("foudre")
    # entrées complètes, groupées par registre
    assert "catalog/catalog.yaml" in out and "upstream" in out
    assert "inventory/tables.yaml" in out and "notif_foudre.php" in out  # readers visibles
    assert "inventory/pipelines.yaml" in out and "cron.recup-blitzortung" in out
    assert "lineage/jobs.yaml" in out
    assert "contracts/foudre.odcs.yaml" in out
    # les entrées non concernées ne fuient pas
    assert "cron.autre" not in out and "autre.odcs.yaml" not in out


def test_lineage_no_match(tmp_path):
    from ic_data_bot.tools import ToolBox

    tb = ToolBox(_lineage_snapshot(tmp_path))
    assert "Aucune référence" in tb.lineage("inexistant_xyz")


def test_lineage_caps_entries(tmp_path):
    from ic_data_bot.tools import ToolBox

    inv = tmp_path / "inventory"; inv.mkdir(parents=True)
    entries = "\n".join(f"  - name: t{i}_commun\n    notes: x" for i in range(10))
    (inv / "tables.yaml").write_text(f"version: 1\ntables:\n{entries}\n")
    out = ToolBox(tmp_path).lineage("commun")
    assert "10 entrée(s)" in out
    assert "affine avec grep" in out  # 6 affichées, 4 signalées


def test_lineage_includes_ops_overlay(tmp_path):
    from ic_data_bot.tools import ToolBox

    root = _lineage_snapshot(tmp_path)
    opsdir = root / "_ops"; opsdir.mkdir()
    (opsdir / "ops-mapping.yaml").write_text(
        "version: 1\nops_storage_systems:\n"
        "  - id: mariadb-prod\n    host_ip: 192.0.2.1 (ct-mariadb-1)\n"
        "ops_pipelines:\n"
        "  - id: cron.recup-blitzortung\n    notes: chemin reel /var/scripts/foudre\n"
    )
    out = ToolBox(root).lineage("foudre")
    assert "_ops/ops-mapping.yaml" in out
    assert "/var/scripts/foudre" in out      # le delta ops rejoint la jointure
    out2 = ToolBox(root).lineage("mariadb-prod")
    assert "192.0.2.1" in out2
