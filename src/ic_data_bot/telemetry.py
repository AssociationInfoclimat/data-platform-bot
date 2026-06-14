from __future__ import annotations

import re

# Rédaction des détails internes avant envoi à Langfuse Cloud (tiers) : IP et
# domaines homelab. Les réponses ops du bot exposent ces infos — on les masque
# pour garder l'observabilité sans exfiltrer l'infra. Désactivable (LANGFUSE_REDACT=0).
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_HOST_RE = re.compile(r"\b[\w.-]*(?:home\.ponf|chom\.ovh|vcs\.infoclimat)\b", re.IGNORECASE)


def redact(text: str | None, enabled: bool = True) -> str | None:
    if not enabled or not text:
        return text
    text = _IP_RE.sub("‹ip-interne›", text)
    text = _HOST_RE.sub("‹host-interne›", text)
    return text


def make_langfuse(cfg):
    """Client Langfuse si les clés sont présentes, sinon None (no-op). Ne lève jamais."""
    if not (cfg.langfuse_public_key and cfg.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=cfg.langfuse_public_key,
            secret_key=cfg.langfuse_secret_key,
            host=cfg.langfuse_host or "https://cloud.langfuse.com",
        )
        print(f"[langfuse] actif → {cfg.langfuse_host} "
              f"(rédaction infra : {'on' if cfg.langfuse_redact else 'OFF'})", flush=True)
        return client
    except Exception as exc:
        print(f"[langfuse] init KO : {type(exc).__name__} — désactivé", flush=True)
        return None
