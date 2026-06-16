import gzip

from ic_data_bot.tools import ToolBox


def _snap(tmp_path):
    vol = tmp_path / "audits" / "volumetrie"
    vol.mkdir(parents=True)
    (vol / "inventaire-20260101.csv").write_text(
        "system,database,table,row_estimate,data_bytes,index_bytes,total_bytes,extra\n"
        "mariadb,V5,foudre,108190981,25143244836,8857620480,34000865316,\n"
        "mariadb,V5,autre,42,1,1,2,\n", encoding="utf-8")
    sch = tmp_path / "schemas" / "mariadb"
    sch.mkdir(parents=True)
    ddl = "CREATE TABLE `foudre` (\n  `dh_usec` int(50) UNSIGNED NOT NULL,\n  `key` varchar(32)\n);\n"
    (sch / "schema.sql.gz").write_bytes(gzip.compress(ddl.encode("utf-8")))
    return tmp_path


def test_volumetrie_returns_audited_count_with_date(tmp_path):
    out = ToolBox(_snap(tmp_path)).volumetrie("foudre")
    assert "108 190 981" in out
    assert "2026-01-01" in out
    assert "snapshot" in out.lower() or "audit" in out.lower()


def test_volumetrie_unknown(tmp_path):
    out = ToolBox(_snap(tmp_path)).volumetrie("zzz_nope")
    assert "aucune" in out.lower()


def test_schema_returns_ddl(tmp_path):
    out = ToolBox(_snap(tmp_path)).schema("foudre")
    assert "CREATE TABLE" in out and "int(50) UNSIGNED" in out


def test_schema_unknown(tmp_path):
    out = ToolBox(_snap(tmp_path)).schema("nope")
    assert "aucun ddl" in out.lower()


def test_public_mode_still_serves_vol_and_schema(tmp_path):
    """volumetrie/schema servent du corpus public (pas _ops) → OK même en mode public."""
    tb = ToolBox(_snap(tmp_path), public=True)
    assert "108 190 981" in tb.volumetrie("foudre")
    assert "CREATE TABLE" in tb.schema("foudre")
