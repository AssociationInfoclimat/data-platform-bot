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


# ── Tools ──────────────────────────────────────────────────────────────────
@mcp.tool()
def read_file(path: str) -> str:
    """Lit un fichier du snapshot data-platform (chemin relatif, ex.
    contracts/foudre.odcs.yaml). Tronqué si volumineux. Les chemins _ops/ sont refusés."""
    try:
        return _tb.read_file(path)
    except ToolError as exc:
        return str(exc)


@mcp.tool()
def grep(pattern: str, glob: str = "**/*") -> str:
    """Recherche une regex dans le snapshot (filtre glob optionnel, ex. inventory/*.yaml).
    Retourne fichier:ligne: extrait, plafonné. Les fichiers _ops/ sont ignorés."""
    try:
        return _tb.grep(pattern, glob)
    except ToolError as exc:
        return str(exc)


@mcp.tool()
def lineage(name: str) -> str:
    """Analyse d'impact : entrées complètes des registres (catalog, tables, pipelines,
    sources, stockage, jobs) + contrats mentionnant `name`. Pour « qui lit/écrit X »,
    « qu'est-ce qui dépend de X », statut mort/douteux. L'overlay _ops/ est exclu."""
    try:
        return _tb.lineage(name)
    except ToolError as exc:
        return str(exc)


@mcp.tool()
def list_corpus(subdir: str = "") -> str:
    """Liste les fichiers du corpus data-platform (sous subdir optionnel) pour découverte,
    en excluant _ops/. Utiliser ensuite read_file/resource pour lire."""
    root = Path(SNAPSHOT_DIR).resolve()
    base = (root / subdir).resolve()
    if base != root and not str(base).startswith(str(root) + os.sep):
        return f"Chemin refusé : {subdir}"
    out = []
    for fp in sorted(base.rglob("*")):
        if fp.is_file() and "_ops" not in fp.relative_to(root).parts and ".git" not in fp.parts:
            out.append(str(fp.relative_to(root)))
    return "\n".join(out) if out else "Aucun fichier."


# ── Resource (lecture par URI) ──────────────────────────────────────────────
@mcp.resource("dataplatform://{path}")
def corpus_resource(path: str) -> str:
    """Contenu d'un fichier du corpus data-platform (URI dataplatform://<chemin>)."""
    try:
        return _tb.read_file(path)
    except ToolError as exc:
        return str(exc)


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


def _build_app():
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            token = os.environ.get("MCP_BEARER_TOKEN", "")
            if not token:
                return JSONResponse({"error": "server misconfigured: no token"}, status_code=503)
            if request.headers.get("authorization", "") != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuth)
    app.router.routes.append(Route("/healthz", lambda r: PlainTextResponse("ok")))
    return app


def run() -> None:  # pragma: no cover (point d'entrée I/O)
    import uvicorn

    _sync_corpus()
    threading.Thread(target=_refresh_loop, daemon=True).start()
    print(f"[mcp] data-platform MCP sur {MCP_HOST}:{MCP_PORT} (snapshot {SNAPSHOT_DIR})", flush=True)
    uvicorn.run(_build_app(), host=MCP_HOST, port=MCP_PORT, log_level="info")


if __name__ == "__main__":
    run()
