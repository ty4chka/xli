#!/usr/bin/env python3
"""
XLI Config v5 - with provider selection and mode settings
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.config")


class Config:
    """XLI Configuration"""

    DEFAULTS = {
        "provider": "mistral",
        "model": "mistral-large-latest",
        "temperature": 0.4,
        "max_tokens": 4000,
        "mode": "headless",
        "use_cache": True,
        "use_mcp": True,
        "max_steps": 50,
        "team": "default",
        "project": "default",
        "sandbox_timeout": 10,
        "sandbox_max_memory_mb": 256,
        "sandbox_disable_network": True,
    }

    def __init__(self):
        self.config_dir = Path.home() / ".xli"
        self.config_file = self.config_dir / "config.json"
        self.env_file = self.config_dir / ".env"
        self._config = self.DEFAULTS.copy()
        self._load()

    def _load(self):
        """Load config from file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    loaded = json.load(f)
                    self._config.update(loaded)
            except Exception as e:
                logger.log_error("config", "Failed to load config", exc=e)

        # Load env
        if self.env_file.exists():
            try:
                with open(self.env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key] = value.strip('"').strip("'")
            except Exception:
                pass

    def save(self):
        """Save config to file"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w") as f:
            json.dump(self._config, f, indent=2)

    def get(self, key: str, default=None):
        """Get config value"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set config value"""
        self._config[key] = value
        self.save()

    def get_provider(self) -> str:
        return self.get("provider", "mistral")

    def get_model(self) -> str:
        return self.get("model", "mistral-large-latest")

    def get_temperature(self) -> float:
        return self.get("temperature", 0.4)

    def get_max_tokens(self) -> int:
        return self.get("max_tokens", 4000)

    def get_mode(self) -> str:
        return self.get("mode", "headless")

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for provider"""
        key_map = {
            "mistral": "MISTRAL_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "google": "GEMINI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_key = key_map.get(provider, f"{provider.upper()}_API_KEY")
        return os.environ.get(env_key)

    def get_default_provider(self) -> str:
        return self.get_provider()

    def get_team(self) -> str:
        return self.get("team", "default")

    def get_project(self) -> str:
        return self.get("project", "default")

    def get_sandbox_timeout(self) -> int:
        return self.get("sandbox_timeout", 10)

    def get_sandbox_max_memory(self) -> int:
        """Max memory for sandboxed code, in MB."""
        return self.get("sandbox_max_memory_mb", 256)

    def is_network_disabled(self) -> bool:
        return self.get("sandbox_disable_network", True)


# Singleton
_config_instance = None


def get_config() -> Config:
    """Get config singleton"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
