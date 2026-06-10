from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SPARSE_PATHS = ["data-platform/"]


def build_clone_url(repo_url: str, username: str, token: str) -> str:
    """Injecte user:token dans une URL https. Lève ValueError si incomplet.

    L'URL retournée contient un secret — ne jamais la loguer.
    """
    if not username or not token:
        raise ValueError("username et token requis pour l'URL de clone")
    parts = urlsplit(repo_url)
    if parts.scheme != "https":
        raise ValueError("repo_url doit être en https")
    host = parts.hostname or ""
    netloc = f"{username}:{token}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def sync(clone_dir, repo_url: str, username: str, token: str, branch: str,
         sparse_paths=SPARSE_PATHS, runner=subprocess.run) -> None:
    """Clone (sparse/partial) si absent, sinon pull --ff-only. Le token n'est
    jamais persisté dans .git/config (origin remis sur l'URL propre après clone ;
    pull avec URL authentifiée inline)."""
    clone_dir = Path(clone_dir)
    auth_url = build_clone_url(repo_url, username, token)
    if not (clone_dir / ".git").is_dir():
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        runner(["git", "clone", "--filter=blob:none", "--sparse", "--branch", branch,
                auth_url, str(clone_dir)], check=True)
        runner(["git", "-C", str(clone_dir), "sparse-checkout", "set", *sparse_paths], check=True)
        runner(["git", "-C", str(clone_dir), "remote", "set-url", "origin", repo_url], check=True)
    else:
        runner(["git", "-C", str(clone_dir), "pull", "--ff-only", auth_url, branch], check=True)


def main() -> int:
    repo_url = os.environ["REPO_URL"]
    username = os.environ["GITLAB_DEPLOY_USER"]
    token = os.environ["GITLAB_DEPLOY_TOKEN"]
    clone_dir = Path(os.environ["CLONE_DIR"])
    branch = os.environ.get("REPO_BRANCH", "feat/data-platform-bootstrap")
    existed = (clone_dir / ".git").is_dir()
    try:
        sync(clone_dir, repo_url, username, token, branch)
        print(f"[gitsync] OK ({'pull' if existed else 'clone'}) -> {clone_dir}")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"[gitsync] échec git (code {exc.returncode})", file=sys.stderr)
        return 0 if existed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
