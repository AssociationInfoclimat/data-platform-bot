import subprocess

import pytest
from ic_data_bot import gitsync
from ic_data_bot.gitsync import build_clone_url, sync

def test_build_clone_url_injects_credentials():
    url = build_clone_url("https://gitlab.example.com/grp/site.git", "deploy", "secret")
    assert url == "https://deploy:secret@gitlab.example.com/grp/site.git"

def test_build_clone_url_keeps_port():
    url = build_clone_url("https://host:8443/grp/site.git", "u", "t")
    assert url == "https://u:t@host:8443/grp/site.git"

def test_build_clone_url_requires_token():
    with pytest.raises(ValueError):
        build_clone_url("https://host/x.git", "u", "")

def test_build_clone_url_requires_user():
    with pytest.raises(ValueError):
        build_clone_url("https://host/x.git", "", "t")

def test_build_clone_url_requires_https():
    with pytest.raises(ValueError):
        build_clone_url("http://host/x.git", "u", "t")

class _Runner:
    def __init__(self): self.calls = []
    def __call__(self, args, **kw):
        self.calls.append(args)

def test_sync_clones_when_absent(tmp_path):
    r = _Runner()
    clone_dir = tmp_path / "repo"   # pas de .git -> clone
    sync(clone_dir, "https://host/x.git", "u", "t", "main", runner=r)
    assert r.calls[0][:3] == ["git", "clone", "--filter=blob:none"]
    assert "--sparse" not in r.calls[0]
    assert "--branch" in r.calls[0] and "main" in r.calls[0]
    assert "https://u:t@host/x.git" in r.calls[0]
    assert any(c[:3] == ["git", "-C", str(clone_dir)] and "set-url" in c and "https://host/x.git" in c
               for c in r.calls)

def test_sync_clones_anonymously_without_creds(tmp_path):
    r = _Runner()
    clone_dir = tmp_path / "repo"   # repo public -> pas de créds
    sync(clone_dir, "https://host/x.git", "", "", "main", runner=r)
    assert "https://host/x.git" in r.calls[0]
    assert not any("@" in str(a) for a in r.calls[0])

def test_sync_pulls_when_present(tmp_path):
    r = _Runner()
    clone_dir = tmp_path / "repo"
    (clone_dir / ".git").mkdir(parents=True)   # .git present -> pull
    sync(clone_dir, "https://host/x.git", "u", "t", "main", runner=r)
    assert r.calls[0][:4] == ["git", "-C", str(clone_dir), "fetch"]
    assert r.calls[1][:5] == ["git", "-C", str(clone_dir), "reset", "--hard"]
    assert "FETCH_HEAD" in r.calls[1]
    assert "https://u:t@host/x.git" in r.calls[0] and "main" in r.calls[0]


def _set_env(monkeypatch, clone_dir):
    monkeypatch.setenv("REPO_URL", "https://host/x.git")
    monkeypatch.setenv("GITLAB_DEPLOY_USER", "u")
    monkeypatch.setenv("GITLAB_DEPLOY_TOKEN", "SECRETTOKEN")
    monkeypatch.setenv("CLONE_DIR", str(clone_dir))

def test_main_success_prints_only_dir(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch, tmp_path / "repo")
    monkeypatch.setattr(gitsync, "sync", lambda *a, **k: None)
    rc = gitsync.main()
    out = capsys.readouterr()
    assert rc == 0
    assert "SECRETTOKEN" not in out.out and "SECRETTOKEN" not in out.err

def test_main_failure_when_absent_returns_1_without_leaking_token(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch, tmp_path / "repo")   # pas de .git -> clone absent
    def boom(*a, **k):
        raise subprocess.CalledProcessError(
            128, ["git", "clone", "https://u:SECRETTOKEN@host/x.git"])
    monkeypatch.setattr(gitsync, "sync", boom)
    rc = gitsync.main()
    out = capsys.readouterr()
    assert rc == 1
    assert "SECRETTOKEN" not in out.out and "SECRETTOKEN" not in out.err

def test_main_failure_when_present_returns_0(tmp_path, monkeypatch):
    clone_dir = tmp_path / "repo"
    (clone_dir / ".git").mkdir(parents=True)   # .git présent -> échec non bloquant
    _set_env(monkeypatch, clone_dir)
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["git", "pull"])
    monkeypatch.setattr(gitsync, "sync", boom)
    assert gitsync.main() == 0
