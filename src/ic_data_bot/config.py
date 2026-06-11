from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

REQUIRED = ("DISCORD_BOT_TOKEN", "ANTHROPIC_API_KEY", "ALLOWED_CHANNEL_ID")


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    anthropic_api_key: str
    allowed_channel_id: int
    snapshot_dir: str
    model: str
    max_tokens: int
    user_rate_limit: int
    rate_window_seconds: int
    daily_token_budget: int
    tool_read_max_bytes: int
    budget_state_path: str
    corrections_path: str
    ops_mapping_path: str
    github_token: str
    github_issues_repo: str
    history_ttl_seconds: int
    history_max_turns: int
    repo_url: str
    gitlab_deploy_user: str
    gitlab_deploy_token: str
    clone_dir: str
    branch: str
    health_port: int
    health_bind: str
    refresh_interval_seconds: int


def load_config(env: Mapping[str, str]) -> Config:
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise ValueError(f"Variables d'environnement manquantes : {', '.join(missing)}")

    def _int(name: str, default: int) -> int:
        raw = env.get(name)
        return int(raw) if raw not in (None, "") else default

    return Config(
        discord_bot_token=env["DISCORD_BOT_TOKEN"],
        anthropic_api_key=env["ANTHROPIC_API_KEY"],
        allowed_channel_id=int(env["ALLOWED_CHANNEL_ID"]),
        snapshot_dir=env.get("DATAPLATFORM_SNAPSHOT_DIR") or "./snapshot",
        model=env.get("MODEL") or "claude-sonnet-4-6",
        max_tokens=_int("MAX_TOKENS", 3000),
        user_rate_limit=_int("USER_RATE_LIMIT", 5),
        rate_window_seconds=_int("RATE_WINDOW_SECONDS", 60),
        daily_token_budget=_int("DAILY_TOKEN_BUDGET", 500_000),
        tool_read_max_bytes=_int("TOOL_READ_MAX_BYTES", 60_000),
        budget_state_path=env.get("BUDGET_STATE_PATH") or "./var/token_budget.json",
        corrections_path=env.get("CORRECTIONS_PATH") or "./var/corrections.jsonl",
        ops_mapping_path=env.get("OPS_MAPPING_PATH") or "./var/ops-mapping.yaml",
        github_token=env.get("GITHUB_TOKEN") or "",
        github_issues_repo=env.get("GITHUB_ISSUES_REPO") or "AssociationInfoclimat/data-platform",
        history_ttl_seconds=_int("HISTORY_TTL_SECONDS", 1800),
        history_max_turns=_int("HISTORY_MAX_TURNS", 5),
        repo_url=env.get("REPO_URL") or "",
        gitlab_deploy_user=env.get("GITLAB_DEPLOY_USER") or "",
        gitlab_deploy_token=env.get("GITLAB_DEPLOY_TOKEN") or "",
        clone_dir=env.get("CLONE_DIR") or "",
        branch=env.get("REPO_BRANCH") or "main",
        health_port=_int("HEALTH_PORT", 8080),
        health_bind=env.get("HEALTH_BIND") or "0.0.0.0",
        refresh_interval_seconds=_int("REFRESH_INTERVAL_SECONDS", 3600),
    )
