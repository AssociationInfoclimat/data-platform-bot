"""Guardrail modèle : caviardage SÉMANTIQUE des secrets dans les extraits de code.

Deuxième couche derrière `tools.redact_secrets()` (regex, déterministe, rapide). Le regex ne
connaît qu'une liste finie de noms de clés ; un secret au nom exotique passe à travers. Ce
guardrail confie l'extrait (déjà rédacté par le regex) à un petit LLM dont l'unique tâche est
de remplacer toute donnée sensible restante par ‹secret-rédacté›.

Appliqué CÔTÉ OUTIL (dans search_code), avant que le modèle qui répond ne voie l'extrait : la
réponse rendue à l'utilisateur ne peut donc plus contenir le secret. En cas d'erreur LLM, on
renvoie le texte reçu INCHANGÉ (déjà passé au regex) — jamais d'exception, jamais de fuite
au-delà de ce que le regex a déjà masqué.
"""

from __future__ import annotations

import os

_SCRUB_PROMPT = (
    "Tu es un FILTRE DE SÉCURITÉ. On te donne un extrait de code source. Renvoie-le "
    "STRICTEMENT à l'identique (mêmes lignes, indentation, commentaires) en remplaçant "
    "UNIQUEMENT la VALEUR de toute donnée sensible par le marqueur littéral ‹secret-rédacté›.\n"
    "Sensible = mot de passe, clé/secret d'API, token, salt, pepper, app/application id, "
    "client id/secret, consumer key/secret, identifiants de connexion (DSN, URL user:pass), "
    "clé privée/PEM, secret de signature/chiffrement, et toute chaîne ressemblant à un secret "
    "en dur (longue chaîne quasi-aléatoire affectée à une constante/variable au nom évocateur).\n"
    "NE masque PAS : noms de variables/fonctions/constantes, chemins, noms de tables/colonnes, "
    "URLs sans identifiants, versions, booléens, nombres ordinaires.\n"
    "N'ajoute, ne retire, ne commente, ne reformate RIEN d'autre. Réponds UNIQUEMENT le code "
    "filtré, sans balise de code ni explication."
)


def _disabled() -> bool:
    return (os.environ.get("CODE_SECRET_LLM_SCRUB") or "1").lower() in ("0", "false", "no")


def make_llm_scrubber(provider: str, *, anthropic_key: str = "", mistral_key: str = "",
                      model: str | None = None):
    """Construit une fonction `text -> texte caviardé`, ou None si désactivée/indispo."""
    if _disabled():
        return None
    provider = (provider or "anthropic").lower()

    if provider == "mistral":
        if not mistral_key:
            return None
        from mistralai.client import Mistral
        client = Mistral(api_key=mistral_key)
        mdl = model or os.environ.get("SECRET_SCRUB_MODEL") or "mistral-small-latest"

        def scrub(text: str) -> str:
            if not text:
                return text
            try:
                resp = client.chat.complete(
                    model=mdl, temperature=0, max_tokens=8000,
                    messages=[{"role": "system", "content": _SCRUB_PROMPT},
                              {"role": "user", "content": text}],
                )
                c = resp.choices[0].message.content
                out = c if isinstance(c, str) else "".join(getattr(p, "text", "") for p in c)
                out = out.strip()
                # garde-fou anti-troncature : on ne remplace que si la sortie est plausible
                return out if out and len(out) >= len(text) * 0.4 else text
            except Exception:
                return text
        return scrub

    if not anthropic_key:
        return None
    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_key)
    mdl = model or os.environ.get("SECRET_SCRUB_MODEL") or "claude-haiku-4-5"

    def scrub(text: str) -> str:
        if not text:
            return text
        try:
            resp = client.messages.create(
                model=mdl, max_tokens=8000, temperature=0,
                system=_SCRUB_PROMPT,
                messages=[{"role": "user", "content": text}],
            )
            out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return out if out and len(out) >= len(text) * 0.4 else text
        except Exception:
            return text
    return scrub
