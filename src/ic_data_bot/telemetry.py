from __future__ import annotations

import os
import re

# Rédaction des détails internes avant envoi à Langfuse Cloud (tiers) : IP et
# domaines homelab. Les réponses ops du bot exposent ces infos — on les masque
# pour garder l'observabilité sans exfiltrer l'infra. Désactivable (LANGFUSE_REDACT=0).
#
# Repo PUBLIC : seuls les motifs non sensibles vivent dans le code (toute IP, plus
# le domaine public chom.ovh). Les domaines internes privés sont fournis hors-code
# via EXTRA_REDACT_PATTERNS (CSV) dans le .env de la VM, jamais commités.
# _build_host_re permet de tester le mécanisme sans dépendre de l'env.
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _extra_patterns() -> tuple[str, ...]:
    return tuple(p.strip() for p in os.environ.get("EXTRA_REDACT_PATTERNS", "").split(",") if p.strip())


def _build_host_re(extra: tuple[str, ...] = ()) -> re.Pattern:
    alts = "|".join(re.escape(d) for d in ("chom.ovh", *extra))
    return re.compile(rf"\b[\w.-]*(?:{alts})\b", re.IGNORECASE)


_HOST_RE = _build_host_re(_extra_patterns())


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


# --- Prompt management (git = source d'autorité, Langfuse = historique + runtime) ---
def register_prompt(lf, name: str, text: str) -> None:
    """Crée une nouvelle version 'production' du prompt `name` dans Langfuse SI le
    texte diffère de la version courante (évite le spam de versions à chaque deploy).
    No-op si lf absent ; ne lève jamais."""
    if lf is None:
        return
    try:
        cur = lf.get_prompt(name, label="production", cache_ttl_seconds=0, fallback="")
        if getattr(cur, "prompt", None) == text:
            return
    except Exception:
        pass
    try:
        lf.create_prompt(name=name, prompt=text, labels=["production"], type="text",
                         commit_message="sync depuis le code")
        print(f"[langfuse] prompt '{name}' versionné (label production)", flush=True)
    except Exception as exc:
        print(f"[langfuse] create_prompt {name} KO : {type(exc).__name__}", flush=True)


def resolve_prompt(lf, name: str, fallback: str) -> str:
    """Texte du prompt 'production' depuis Langfuse (cache TTL 5 min), sinon le
    fallback (code). Ne lève jamais → la coupure Langfuse ne casse pas le bot."""
    if lf is None:
        return fallback
    try:
        return lf.get_prompt(name, label="production", fallback=fallback,
                             cache_ttl_seconds=300).prompt
    except Exception:
        return fallback
