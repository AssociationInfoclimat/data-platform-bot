from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ic_data_bot.kestra_events import KestraEventLog
from ic_data_bot.bot import discord_message_text
from ic_data_bot.tools import ToolBox, ToolError

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def _log():
    return KestraEventLog(now=lambda: NOW)


def test_record_filter_and_order():
    log = _log()
    log.record(NOW - timedelta(minutes=30), "system-events", "✅ infoclimat.data.refresh-materialized-views SUCCESS")
    log.record(NOW - timedelta(minutes=3), "system-events", "✅ infoclimat.data.recup-mf SUCCESS")
    log.record(NOW - timedelta(hours=2), "alerts", "❌ infoclimat.data.refresh-materialized-views FAILED")
    out = log.recent("refresh-materialized")
    assert "2 événement(s)" in out
    # plus récent d'abord : le succès (30 min) avant l'échec (2 h)
    assert out.index("il y a 30 min") < out.index("il y a 2,0 h")
    assert "recup-mf" not in out
    # consigne de jointure in-band (leçon E3)
    assert "quality: freshness" in out


def test_prune_48h_and_empty_messages():
    log = _log()
    log.record(NOW - timedelta(hours=49), "alerts", "vieux FAILED")
    log.record(NOW - timedelta(hours=1), "alerts", "récent FAILED")
    log.record(NOW, "alerts", "   ")          # vide → ignoré
    assert log.count() == 1
    assert "vieux" not in log.recent()


def test_no_events_and_no_match_warn_uncertainty():
    log = _log()
    assert "silence ≠ tout va bien" in log.recent("climato")
    log.record(NOW, "system-events", "✅ autre-flow SUCCESS")
    out = log.recent("climato")
    assert "Aucun des 1 événements" in out
    assert "incertitude" in out


def test_discord_message_text_extracts_embeds():
    msg = SimpleNamespace(
        content="",
        embeds=[SimpleNamespace(
            title="Kestra workflow",
            description="infoclimat.data.recup-mf — SUCCESS",
            fields=[SimpleNamespace(name="Durée", value="41s")],
        )],
    )
    text = discord_message_text(msg)
    assert "recup-mf" in text and "Durée: 41s" in text


def test_toolbox_dispatch(tmp_path):
    log = _log()
    log.record(NOW, "alerts", "❌ flow-x FAILED")
    tb = ToolBox(tmp_path, kestra_log=log)
    assert "flow-x" in tb.dispatch("kestra_recent", {"query": "flow-x"})
    tb2 = ToolBox(tmp_path)  # non configuré
    try:
        tb2.dispatch("kestra_recent", {})
        assert False, "ToolError attendue"
    except ToolError:
        pass
