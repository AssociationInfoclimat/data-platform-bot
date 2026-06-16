from ic_data_bot.tools import redact_secrets

# Jetons factices CONSTRUITS au runtime : le fichier source ne contient pas de littéral
# ressemblant à un vrai token (évite le secret-scanning GitHub) et pas d'IP infra.
_GHP = "ghp_" + "a1b2c3d4e5" * 4
_SKANT = "sk-ant-" + "x7y8z9w0" * 3

SECRETS = [
    ("define('DB_PASSWORD', 'sup3rSecret');", "sup3rSecret"),
    ("'api_key' => 'abcd1234efghIJKL',", "abcd1234efghIJKL"),
    ('"token": "eyJhbGciOiJ"', "eyJhbGciOiJ"),
    ('password = "hunter2pass"', "hunter2pass"),
    ("$mdp = 'tr0ub4dor3'", "tr0ub4dor3"),
    ("DB_PASSWORD=pr0dpass123", "pr0dpass123"),
    ("MYSQL_SECRET_KEY=zzz9999aaaa", "zzz9999aaaa"),
    ("url = 'mysql://root:r00tPassWd@dbhost/db'", "r00tPassWd"),
    (f"key {_SKANT} here", _SKANT),
    (f"tok {_GHP} x", _GHP),
    # forme PHP `const NOM = "valeur";` (modèle des fuites salt / app id constatées)
    ('const COOKIE_HASH_SALT = "Gk9zQ2pLm";', "Gk9zQ2pLm"),
    ('const SVC_APPLICATION_ID = "abc123APPID";', "abc123APPID"),
    ("$client_secret = 'oauthS3cr3tVal';", "oauthS3cr3tVal"),
    ("CONSUMER_SECRET=tw1tterSecretXYZ", "tw1tterSecretXYZ"),
    # FUITES CONSTATÉES (valeurs FACTICES, même structure que les vraies) :
    # - salt à suffixe chiffré (USER_SALT1) que l'ancien regex laissait passer
    ('const USER_SALT1 = "Mxk3SaltValAaZ9";', "Mxk3SaltValAaZ9"),
    ('const USER_UNIQUE_SALT = "Qp7RtsVbnHk2Lm";', "Qp7RtsVbnHk2Lm"),
    # - constantes AUTH dont le NOM n'est pas un mot-clé → rattrapées par la haute entropie
    ("const INT_AUTH_API = 'Zx9KdMq2Lp7Tvb';", "Zx9KdMq2Lp7Tvb"),
    ("const EXT_AUTH1 = 'Bc4HmnQ8RsTuWx';", "Bc4HmnQ8RsTuWx"),
]


def test_no_false_positive_on_non_secret_constants():
    """Constantes légitimes à NE PAS caviarder (URLs, licences, versions, statuts)."""
    for benign in [
        "const URL = 'https://example.vercel.app';",
        "const COMMERCIAL_LICENSE = 1;",
        "const ETALAB_LICENSE = 0;",
        "$status = 'actif';",
        "const VERSION = '2024.05.01';",
    ]:
        assert redact_secrets(benign) == benign, f"faux positif: {benign!r}"


def test_no_false_positive_on_camelcase_identifiers():
    """Garde anti-mot : un identifiant « prononçable » (long segment minuscule), même en
    casse mixte avec un chiffre, n'est PAS un secret — la règle d'entropie ne doit pas le
    masquer, sinon search_code rend du code légitime illisible."""
    for benign in [
        "$handler = 'MyClassNameV2Handler';",
        "$tpl = 'UserProfileV2Tpl';",
        "$cls => 'AbstractServiceFactory';",
        "$css = 'btnPrimary2Block';",
    ]:
        assert redact_secrets(benign) == benign, f"faux positif: {benign!r}"


def test_secrets_are_masked():
    for line, secret in SECRETS:
        out = redact_secrets(line)
        assert secret not in out, f"NON masqué: {line!r} → {out!r}"
        assert "‹secret-rédacté›" in out or "‹clé-privée-rédactée›" in out, f"pas de marqueur: {out!r}"


def test_private_key_block_masked():
    blob = "-----BEGIN RSA PRIVATE KEY-----\nMIIEv...secret...\n-----END RSA PRIVATE KEY-----"
    out = redact_secrets(blob)
    assert "secret" not in out and "MIIEv" not in out
    assert "‹clé-privée-rédactée›" in out


def test_llm_scrubber_gating(monkeypatch):
    from ic_data_bot.secret_guard import make_llm_scrubber
    # désactivé explicitement → None même avec une clé
    monkeypatch.setenv("CODE_SECRET_LLM_SCRUB", "0")
    assert make_llm_scrubber("anthropic", anthropic_key="x") is None
    # activé mais sans clé du provider → None (pas de crash)
    monkeypatch.setenv("CODE_SECRET_LLM_SCRUB", "1")
    assert make_llm_scrubber("anthropic", anthropic_key="") is None
    assert make_llm_scrubber("mistral", mistral_key="") is None


def test_no_false_positive_on_code_identifiers():
    """Ne pas caviarder du code légitime sans valeur secrète."""
    for benign in [
        "function getPassword() { return $this->hash; }",
        "$token = nextToken();",
        "// le password est stocké hashé en base",
        "if (verifyToken($t)) { ... }",
    ]:
        assert redact_secrets(benign) == benign, f"faux positif: {benign!r}"
