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
]


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


def test_no_false_positive_on_code_identifiers():
    """Ne pas caviarder du code légitime sans valeur secrète."""
    for benign in [
        "function getPassword() { return $this->hash; }",
        "$token = nextToken();",
        "// le password est stocké hashé en base",
        "if (verifyToken($t)) { ... }",
    ]:
        assert redact_secrets(benign) == benign, f"faux positif: {benign!r}"
