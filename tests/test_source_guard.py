"""Tests de la garde de sourçage et des builders d'URL (bot.py / tools.py)."""
from __future__ import annotations

from ic_data_bot.bot import _has_source, _sanitize_external_urls, _strip_fabricated_urls
from ic_data_bot.tools import _github_web_base, is_internal_url, normalize_url


_WL = {"https://meteo.data.gouv.fr", "https://public-api.meteofrance.fr/public/DPObs/v2"}


def test_external_whitelist_keeps_attested_and_internal():
    src = ("interne : https://github.com/AssociationInfoclimat/data-platform/blob/abc/x.yaml "
           "externe ok : https://meteo.data.gouv.fr/ "
           "base ok : https://public-api.meteofrance.fr")
    out, changed = _sanitize_external_urls(src, _WL)
    assert not changed and out == src


def test_external_whitelist_strips_unattested():
    src = "doc : [Confluence MF](https://confluence.meteofrance.fr/x/854196230) et https://evil.example/x"
    out, changed = _sanitize_external_urls(src, _WL)
    assert changed
    assert "confluence.meteofrance.fr" not in out and "evil.example" not in out
    assert "Confluence MF" in out          # texte du lien conservé
    assert "(lien externe retiré)" in out  # URL nue retirée


def test_external_whitelist_strips_fabricated_specific_path():
    src = "voir https://public-api.meteofrance.fr/public/DPObs/v2/station/horaire?id=1"
    out, changed = _sanitize_external_urls(src, _WL)
    assert changed and "station/horaire" not in out


def test_normalize_and_internal():
    assert normalize_url("https://meteo.data.gouv.fr/#frag") == "https://meteo.data.gouv.fr"
    assert is_internal_url("https://github.com/AssociationInfoclimat/data-platform/blob/x")
    assert is_internal_url("https://vcs.infoclimat.net/responsablestechnique/site-infoclimat")
    assert not is_internal_url("https://meteo.data.gouv.fr/")


def test_strip_fabricated_sha_link():
    src = "Voir [contracts/x.odcs.yaml](https://github.com/AssociationInfoclimat/data-platform/blob/<sha>/contracts/x.odcs.yaml) pour le détail."
    out, changed = _strip_fabricated_urls(src)
    assert changed
    assert "<sha>" not in out and "https://" not in out
    assert "contracts/x.odcs.yaml" in out  # le texte du lien est conservé


def test_strip_fabricated_bare_url():
    out, changed = _strip_fabricated_urls("doc : https://confluence.meteofrance.fr/x/<id>")
    assert changed and "(lien retiré)" in out


def test_real_urls_untouched():
    src = ("interne : https://github.com/AssociationInfoclimat/data-platform/blob/007403b4/contracts/x.odcs.yaml "
           "externe : https://meteo.data.gouv.fr/")
    out, changed = _strip_fabricated_urls(src)
    assert not changed and out == src


def test_has_source_detects_url():
    assert _has_source("Voir https://github.com/AssociationInfoclimat/x/blob/abc/f.py#L1-L2")
    assert _has_source("doc : https://public-api.meteofrance.fr/public/DPObs/v2")


def test_has_source_detects_path_lines():
    assert _has_source("c'est dans site-infoclimat/include/combined.php:35-36")
    assert _has_source("inventory/pipelines.yaml:42 décrit le flow")


def test_has_source_false_when_none():
    assert not _has_source("Le vent est mesuré en m/s, converti ensuite.")
    assert not _has_source("")
    assert not _has_source("Je ne sais pas, peux-tu préciser ?")


def test_github_web_base():
    assert _github_web_base("https://github.com/AssociationInfoclimat/data-platform.git") == \
        "https://github.com/AssociationInfoclimat/data-platform"
    assert _github_web_base("git@github.com:AssociationInfoclimat/data-platform.git") == \
        "https://github.com/AssociationInfoclimat/data-platform"
    assert _github_web_base("") == ""
    assert _github_web_base("ssh://git@vcs.infoclimat.net:59833/grp/repo.git") == ""
