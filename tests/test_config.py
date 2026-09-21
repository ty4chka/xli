#!/usr/bin/env python3
"""Config basics.

There is one config system: xli.manager.config. The legacy xli.core.config was
a second, parallel one that read only ~/.xli/config.json and ~/.xli/.env and
ignored XLI_* entirely — which is why `XLI_PROVIDER=openai` silently did
nothing while the provider factory consulted it. These tests pin the surviving
behaviour.
"""

import pytest

from xli.manager.config import Config, get_config, reset_config


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XLI_CONFIG_DIR", str(tmp_path))
    reset_config()
    yield
    reset_config()


def test_config_exists():
    assert get_config() is not None


def test_default_provider():
    assert get_config().default_provider() in {
        "mistral", "openai", "anthropic", "claude", "openrouter",
        "gemini", "google", "ollama", "local",
    }


def test_env_overrides_the_default_provider(monkeypatch):
    """The regression: this used to be ignored entirely."""
    monkeypatch.setenv("XLI_PROVIDER", "openai")
    assert Config.load().default_provider() == "openai"


def test_env_overrides_nested_keys(monkeypatch):
    monkeypatch.setenv("XLI_PROVIDER__MODEL", "gpt-4o")
    monkeypatch.setenv("XLI_AGENT__MAX_STEPS", "7")
    config = Config.load()
    assert config.model() == "gpt-4o"
    assert int(config.get("agent.max_steps")) == 7


def test_env_alias_shorthands(monkeypatch):
    monkeypatch.setenv("XLI_MODE", "auto")
    monkeypatch.setenv("XLI_MAX_STEPS", "5")
    config = Config.load()
    assert config.permission_mode() == "auto"
    assert int(config.get("agent.max_steps")) == 5


def test_api_key_lookup(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert Config.load().api_key("openai") == "sk-test"


@pytest.mark.parametrize(
    "alias,var",
    [("claude", "ANTHROPIC_API_KEY"), ("google", "GEMINI_API_KEY")],
)
def test_api_key_aliases(monkeypatch, alias, var):
    monkeypatch.setenv(var, "aliased")
    assert Config.load().api_key(alias) == "aliased"


def test_api_key_absent_is_none(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert Config.load().api_key("mistral") is None


def test_sandbox_accessors():
    config = get_config()
    assert isinstance(config.sandbox_timeout(), int)
    assert isinstance(config.sandbox_max_memory_mb(), int)
    assert isinstance(config.sandbox_network_disabled(), bool)


def test_team_and_project():
    config = get_config()
    assert config.team() == "default"
    assert config.project() == "default"


def test_model_and_sampling_defaults():
    config = get_config()
    assert isinstance(config.model(), str) and config.model()
    assert isinstance(config.temperature(), float)
    assert isinstance(config.max_tokens(), int)
