from ic_data_bot.telemetry import redact, make_langfuse, _build_host_re


def test_redact_masks_internal_details():
    # IP générique + domaine public chom.ovh (les domaines internes privés sont
    # fournis hors-code via EXTRA_REDACT_PATTERNS, testés séparément).
    txt = "MariaDB sur 203.0.113.7 (ct-mariadb), domaine auth.chom.ovh"
    out = redact(txt)
    assert "203.0.113.7" not in out
    assert "chom.ovh" not in out
    assert "‹ip-interne›" in out and "‹host-interne›" in out
    # hostname non sensible conservé
    assert "ct-mariadb" in out


def test_redact_extra_domains_configurable():
    # Mécanisme d'extension : un domaine interne fourni hors-code est bien masqué.
    rx = _build_host_re(("internal.example.invalid",))
    assert rx.sub("‹host-interne›", "git.internal.example.invalid") == "‹host-interne›"


def test_redact_disabled_passthrough():
    txt = "IP 203.0.113.1"
    assert redact(txt, enabled=False) == txt
    assert redact(None) is None


def test_make_langfuse_none_without_keys():
    class _Cfg:
        langfuse_public_key = ""
        langfuse_secret_key = ""
        langfuse_host = "https://cloud.langfuse.com"
        langfuse_redact = True
    assert make_langfuse(_Cfg()) is None
