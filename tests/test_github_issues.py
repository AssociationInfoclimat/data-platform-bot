from ic_data_bot.github_issues import (
    contains_internal_details,
    issue_body,
    _build_internal_re,
)


def test_internal_details_detection():
    # Plages RFC1918 génériques + domaine public chom.ovh (en dur dans le code).
    assert contains_internal_details("la base est sur 192.168.0.5")
    assert contains_internal_details("le vrai host est 10.0.0.9")
    assert contains_internal_details("domaine Chom.OVH")
    assert not contains_internal_details("la colonne toto est de type B")
    assert not contains_internal_details("le job tourne sur ct-timescale")  # hostname public
    assert not contains_internal_details("")


def test_internal_details_extra_configurable():
    # Domaines internes privés fournis hors-code (EXTRA_REDACT_PATTERNS) : mécanisme.
    rx = _build_internal_re(("internal.example.invalid",))
    assert rx.search("voir git.internal.example.invalid")


def test_issue_body_structure():
    body = issue_body("pam", "toto est de type B", "toto est de type A", 3)
    assert "> toto est de type B" in body
    assert "> toto est de type A" in body
    assert "!unfix 3" in body
    body2 = issue_body("pam", "x", None, 1)
    assert "Réponse du bot" not in body2
