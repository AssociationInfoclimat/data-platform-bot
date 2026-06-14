from ic_data_bot.telemetry import redact, make_langfuse


def test_redact_masks_internal_details():
    txt = "MariaDB sur 192.168.0.100 (ct-mariadb), domaine auth.home.ponf, repo vcs.infoclimat.net"
    out = redact(txt)
    assert "192.168.0.100" not in out
    assert "home.ponf" not in out and "vcs.infoclimat" not in out
    assert "‹ip-interne›" in out and "‹host-interne›" in out
    # hostname non sensible conservé
    assert "ct-mariadb" in out


def test_redact_disabled_passthrough():
    txt = "IP 192.168.0.1"
    assert redact(txt, enabled=False) == txt
    assert redact(None) is None


def test_make_langfuse_none_without_keys():
    class _Cfg:
        langfuse_public_key = ""
        langfuse_secret_key = ""
        langfuse_host = "https://cloud.langfuse.com"
        langfuse_redact = True
    assert make_langfuse(_Cfg()) is None
