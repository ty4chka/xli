#!/usr/bin/env python3
"""
XLI Providers Base — AbstractProvider + factory get_provider()
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any

from xli.core.config import get_config
from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.providers")


class AbstractProvider(ABC):
    """Base class for all LLM providers"""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key
        self.model = model
        self.logger = StructuredLogger(f"xli.providers.{self.__class__.__name__.lower()}")

    @abstractmethod
    async def chat(self, messages: List[Dict], temperature: float = 0.4, 
                   max_tokens: int = 4000) -> str:
        """Send chat completion request"""
        pass

    @abstractmethod
    async def stream(self, messages: List[Dict], temperature: float = 0.4) -> Any:
        """Stream chat completion"""
        pass

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Get embeddings"""
        pass


# Global provider instance cache
_provider_instance = None


def get_provider() -> AbstractProvider:
    """Factory: returns configured provider instance"""
    global _provider_instance

    if _provider_instance is not None:
        return _provider_instance

    config = get_config()
    provider_name = config.get_default_provider()

    logger.log_structured("INFO", "providers", f"Creating provider: {provider_name}")

    provider_classes = {
        "mistral": ("xli.providers.mistral", "MistralProvider", "mistral-large-latest"),
        "openai": ("xli.providers.openai", "OpenAIProvider", None),
        "anthropic": ("xli.providers.anthropic", "AnthropicProvider", None),
        "openrouter": ("xli.providers.openrouter", "OpenRouterProvider", None),
    }

    if provider_name not in provider_classes:
        logger.log_structured("ERROR", "providers", f"Unknown provider: {provider_name}")
        raise ValueError(f"Unknown provider: {provider_name}")

    module_path, class_name, default_model = provider_classes[provider_name]
    api_key = config.get_api_key(provider_name)
    if not api_key:
        logger.log_structured("ERROR", "providers", f"{provider_name} API key not found")
        raise ValueError(f"{provider_name.upper()}_API_KEY not set in environment")

    import importlib
    module = importlib.import_module(module_path)
    provider_cls = getattr(module, class_name)
    kwargs = {"api_key": api_key}
    if default_model:
        kwargs["model"] = default_model
    _provider_instance = provider_cls(**kwargs)

    logger.log_structured("INFO", "providers", f"Provider ready: {provider_name}")
    return _provider_instance


def reset_provider() -> None:
    """Clear the cached provider instance (needed for tests and provider/key switches)."""
    global _provider_instance
    _provider_instance = None
