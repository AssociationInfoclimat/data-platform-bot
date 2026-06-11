from __future__ import annotations

import json
import re
import urllib.request

API = "https://api.github.com"

# Le repo d'issues est PUBLIC : on n'y publie jamais de détails d'infra interne.
# Un !fix qui en contient reste un erratum local (volume privé), sans issue.
INTERNAL_PATTERNS = re.compile(
    r"192\.168\.\d|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|\.home\.ponf|chom\.ovh|vcs\.infoclimat",
    re.IGNORECASE,
)


def contains_internal_details(text: str) -> bool:
    return bool(INTERNAL_PATTERNS.search(text or ""))


def _req(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ic-data-bot",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode() or "{}")


def create_issue(repo: str, token: str, title: str, body: str) -> tuple[int, str]:
    d = _req("POST", f"{API}/repos/{repo}/issues", token, {"title": title, "body": body})
    return d["number"], d["html_url"]


def close_issue(repo: str, token: str, number: int, comment: str | None = None) -> None:
    if comment:
        try:
            _req("POST", f"{API}/repos/{repo}/issues/{number}/comments", token, {"body": comment})
        except Exception:
            pass
    _req("PATCH", f"{API}/repos/{repo}/issues/{number}", token, {"state": "closed"})


def issue_body(author: str, text: str, ref_excerpt: str | None, erratum_index: int) -> str:
    parts = [
        f"**Erratum signalé via le bot Discord** (`!fix`) par {author}.",
        "",
        f"> {text}",
    ]
    if ref_excerpt:
        parts += ["", "Réponse du bot concernée :", f"> {ref_excerpt}"]
    parts += [
        "",
        "---",
        f"L'erratum #{erratum_index} est actif dans le bot en attendant la correction. "
        f"Après merge : `!refresh` puis `!unfix {erratum_index}` dans Discord "
        "(ferme cette issue automatiquement).",
    ]
    return "\n".join(parts)
