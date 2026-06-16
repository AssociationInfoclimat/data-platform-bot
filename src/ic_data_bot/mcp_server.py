"""Serveur MCP `data-platform` (autonome, exposé en prod, consommable par Claude Code).

Réexpose les outils corpus du bot (ToolBox : read_file / grep / lineage) en MODE PUBLIC
(l'overlay confidentiel _ops/ est refusé), plus des Resources (fichiers du corpus) et un
Prompt (persona). Le corpus est le repo PUBLIC data-platform, cloné anonymement via gitsync
dans un snapshot DÉDIÉ (jamais l'overlay _ops). Transport streamable-HTTP + auth bearer.

Lancement : `ic-data-bot-mcp` (console script) ou `python -m ic_data_bot.mcp_server`.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import gitsync
from .context import resolve_persona
from .tools import ToolBox, ToolError

SNAPSHOT_DIR = os.environ.get("DATAPLATFORM_SNAPSHOT_DIR") or "./snapshot"
MCP_PORT = int(os.environ.get("MCP_PORT") or "8765")
MCP_HOST = os.environ.get("MCP_HOST") or "0.0.0.0"
REFRESH_SECONDS = int(os.environ.get("MCP_REFRESH_SECONDS") or "3600")

# ToolBox en MODE PUBLIC : read_file/grep/lineage refusent _ops/ (IP/hosts internes).
_tb = ToolBox(Path(SNAPSHOT_DIR), public=True)

# Protection anti-DNS-rebinding du SDK (validation du Host) : derrière un reverse-proxy,
# le Host devient le domaine public. On l'autorise via MCP_ALLOWED_HOSTS (CSV, hors repo).
# Sans cette var (ex. dev local) → protection désactivée : on s'appuie sur Traefik + bearer.
_allowed_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
if _allowed_hosts:
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_hosts,
    )
else:
    _security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP("data-platform", stateless_http=True, transport_security=_security)


# ── Observabilité Langfuse + identité par token ─────────────────────────────
import contextvars as _cv
import hashlib as _hashlib

from .telemetry import make_langfuse

_current_user = _cv.ContextVar("mcp_user", default="anonymous")


class _LfCfg:
    langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host = os.environ.get("LANGFUSE_HOST", "")
    langfuse_redact = os.environ.get("LANGFUSE_REDACT", "1") not in ("0", "false", "")


_lf = make_langfuse(_LfCfg())


def _token_labels() -> dict[str, str]:
    """MCP_TOKEN_LABELS = CSV de paires token:nom (ex. tok1:alice,tok2:bob)."""
    out: dict[str, str] = {}
    for pair in os.environ.get("MCP_TOKEN_LABELS", "").split(","):
        tok, sep, name = pair.partition(":")
        if sep and tok.strip():
            out[tok.strip()] = name.strip()
    return out


def _identity(token: str) -> str:
    """Libellé du porteur : nom mappé sinon id stable anonyme (hash court, jamais le token)."""
    if not token:
        return "anonymous"
    return _token_labels().get(token) or ("user-" + _hashlib.sha256(token.encode()).hexdigest()[:8])


def _traced(tool: str, inp: dict, fn):
    """Exécute fn() et trace l'appel (outil, args, sortie, user) dans Langfuse. Le mask du
    client rédige à l'ingestion. Ne casse jamais l'outil si Langfuse échoue."""
    if _lf is None:
        return fn()
    try:
        with _lf.start_as_current_observation(
            name=f"mcp.{tool}", as_type="span", input=inp,
            metadata={"transport": "mcp", "user": _current_user.get()},
        ) as span:
            out = fn()
            try:
                span.update(output=out)
            except Exception:
                pass
            return out
    except Exception:
        return fn()


# ── Tools ──────────────────────────────────────────────────────────────────
def _safe(method, *args) -> str:
    try:
        return method(*args)
    except ToolError as exc:
        return str(exc)


@mcp.tool()
def read_file(path: str) -> str:
    """Lit un fichier du snapshot data-platform (chemin relatif, ex.
    contracts/foudre.odcs.yaml). Tronqué si volumineux. Les chemins _ops/ sont refusés."""
    return _traced("read_file", {"path": path}, lambda: _safe(_tb.read_file, path))


@mcp.tool()
def grep(pattern: str, glob: str = "**/*") -> str:
    """Recherche une regex dans le snapshot (filtre glob optionnel, ex. inventory/*.yaml).
    Retourne fichier:ligne: extrait, plafonné. Les fichiers _ops/ sont ignorés."""
    return _traced("grep", {"pattern": pattern, "glob": glob}, lambda: _safe(_tb.grep, pattern, glob))


@mcp.tool()
def lineage(name: str) -> str:
    """Analyse d'impact : entrées complètes des registres (catalog, tables, pipelines,
    sources, stockage, jobs) + contrats mentionnant `name`. Pour « qui lit/écrit X »,
    « qu'est-ce qui dépend de X », statut mort/douteux. L'overlay _ops/ est exclu."""
    return _traced("lineage", {"name": name}, lambda: _safe(_tb.lineage, name))


@mcp.tool()
def list_corpus(subdir: str = "") -> str:
    """Liste les fichiers du corpus data-platform (sous subdir optionnel) pour découverte,
    en excluant _ops/. Utiliser ensuite read_file/resource pour lire."""
    def _do():
        root = Path(SNAPSHOT_DIR).resolve()
        base = (root / subdir).resolve()
        if base != root and not str(base).startswith(str(root) + os.sep):
            return f"Chemin refusé : {subdir}"
        out = []
        for fp in sorted(base.rglob("*")):
            if fp.is_file() and "_ops" not in fp.relative_to(root).parts and ".git" not in fp.parts:
                out.append(str(fp.relative_to(root)))
        return "\n".join(out) if out else "Aucun fichier."
    return _traced("list_corpus", {"subdir": subdir}, _do)


# ── Resource (lecture par URI) ──────────────────────────────────────────────
@mcp.resource("dataplatform://{path}")
def corpus_resource(path: str) -> str:
    """Contenu d'un fichier du corpus data-platform (URI dataplatform://<chemin>)."""
    return _traced("resource", {"path": path}, lambda: _safe(_tb.read_file, path))


# ── Prompt (persona) ────────────────────────────────────────────────────────
@mcp.prompt()
def data_manager_persona() -> str:
    """Persona « manager de la donnée Infoclimat » (mêmes garde-fous que le bot) à adopter
    pour répondre aux questions sur la plateforme data via ces outils."""
    return resolve_persona()


# ── Corpus : clone anonyme (public) + refresh périodique ────────────────────
def _sync_corpus() -> None:
    repo_url = os.environ.get("REPO_URL") or ""
    branch = os.environ.get("REPO_BRANCH") or "main"
    if not repo_url:
        print("[mcp] REPO_URL absent — snapshot utilisé tel quel", flush=True)
        return
    try:
        # username/token vides → clone ANONYME (repo public, jamais d'overlay _ops)
        gitsync.sync(SNAPSHOT_DIR, repo_url, "", "", branch)
        print("[mcp] corpus synchronisé", flush=True)
    except Exception as exc:
        print(f"[mcp] gitsync KO : {type(exc).__name__}: {exc}", flush=True)


def _refresh_loop() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        _sync_corpus()


def _valid_tokens() -> set[str]:
    """Tokens bearer acceptés : MCP_BEARER_TOKEN (mono) + MCP_BEARER_TOKENS (CSV, un par
    personne → révocable individuellement). Relu à chaque requête (restart pour appliquer)."""
    raw = os.environ.get("MCP_BEARER_TOKEN", "") + "," + os.environ.get("MCP_BEARER_TOKENS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def _build_app():
    import hmac
    import json

    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/healthz", lambda r: PlainTextResponse("ok")))

    # Middleware ASGI PURE (pas BaseHTTPMiddleware) : auth bearer + pose l'identité dans
    # un contextvar AVANT l'app, pour qu'elle propage jusqu'aux outils (tracing Langfuse).
    class AuthASGI:
        def __init__(self, inner):
            self.inner = inner

        async def _deny(self, send, status: int, msg: str):
            body = json.dumps({"error": msg}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("path") == "/healthz":
                return await self.inner(scope, receive, send)
            tokens = _valid_tokens()
            if not tokens:
                return await self._deny(send, 503, "no token configured")
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            presented = auth[7:] if auth.startswith("Bearer ") else ""
            if not any(hmac.compare_digest(presented, t) for t in tokens):
                return await self._deny(send, 401, "unauthorized")
            _current_user.set(_identity(presented))
            return await self.inner(scope, receive, send)

    return AuthASGI(app)


def run() -> None:  # pragma: no cover (point d'entrée I/O)
    import uvicorn

    _sync_corpus()
    threading.Thread(target=_refresh_loop, daemon=True).start()
    print(f"[mcp] data-platform MCP sur {MCP_HOST}:{MCP_PORT} (snapshot {SNAPSHOT_DIR})", flush=True)
    uvicorn.run(_build_app(), host=MCP_HOST, port=MCP_PORT, log_level="info")


if __name__ == "__main__":
    run()
