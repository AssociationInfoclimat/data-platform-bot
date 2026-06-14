import pytest
from ic_data_bot.config import Config, load_config

BASE = {
    "DISCORD_BOT_TOKEN": "tok",
    "ANTHROPIC_API_KEY": "key",
    "ALLOWED_CHANNEL_ID": "123",
}

def test_load_config_minimal_uses_defaults():
    cfg = load_config(BASE)
    assert isinstance(cfg, Config)
    assert cfg.discord_bot_token == "tok"
    assert cfg.allowed_channel_id == 123
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.max_tokens == 3000
    assert cfg.snapshot_dir == "./snapshot"

def test_load_config_missing_required_raises():
    with pytest.raises(ValueError) as e:
        load_config({"DISCORD_BOT_TOKEN": "tok"})
    assert "ANTHROPIC_API_KEY" in str(e.value)

def test_load_config_overrides_and_casts():
    env = {**BASE, "MAX_TOKENS": "1500", "USER_RATE_LIMIT": "10"}
    cfg = load_config(env)
    assert cfg.max_tokens == 1500
    assert cfg.user_rate_limit == 10

def test_load_config_deploy_defaults():
    cfg = load_config(BASE)
    assert cfg.health_port == 8080
    assert cfg.health_bind == "0.0.0.0"
    assert cfg.refresh_interval_seconds == 3600
    assert cfg.branch == "main"
    assert cfg.repo_url == ""
    assert cfg.gitlab_deploy_user == ""
    assert cfg.gitlab_deploy_token == ""
    assert cfg.clone_dir == ""

def test_load_config_deploy_overrides():
    env = {**BASE, "HEALTH_PORT": "9000", "REFRESH_INTERVAL_SECONDS": "600",
           "REPO_URL": "https://git/x.git", "GITLAB_DEPLOY_TOKEN": "tok"}
    cfg = load_config(env)
    assert cfg.health_port == 9000
    assert cfg.refresh_interval_seconds == 600
    assert cfg.repo_url == "https://git/x.git"
    assert cfg.gitlab_deploy_token == "tok"


def test_provider_defaults_anthropic():
    cfg = load_config(BASE)
    assert cfg.provider == "anthropic"
    assert cfg.anthropic_api_key == "key"
    assert cfg.mistral_api_key == ""


def test_provider_mistral_requires_mistral_key():
    env = {"DISCORD_BOT_TOKEN": "tok", "ALLOWED_CHANNEL_ID": "1", "PROVIDER": "mistral"}
    with pytest.raises(ValueError) as e:
        load_config(env)
    assert "MISTRAL_API_KEY" in str(e.value)
    # anthropic key n'est PAS requise quand provider=mistral
    cfg = load_config({**env, "MISTRAL_API_KEY": "mk", "MODEL": "mistral-small-latest"})
    assert cfg.provider == "mistral"
    assert cfg.mistral_api_key == "mk"
    assert cfg.model == "mistral-small-latest"
