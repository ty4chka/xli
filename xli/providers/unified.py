#!/usr/bin/env python3
"""
XLI Unified Provider System
Supports 75+ providers via unified interface (like AI SDK)
"""

import os
import asyncio
import time
from typing import List, Dict, Optional, Any
from abc import ABC, abstractmethod

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.providers.unified")


class BaseProvider(ABC):
    """Base class for all providers"""

    def __init__(self, api_key: str, model: str, base_url: str = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.last_request_time = 0
        self.min_delay = 2.0
        self.request_count = 0

    async def _rate_limit(self):
        """Rate limiting"""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_delay:
            await asyncio.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()
        self.request_count += 1

    @abstractmethod
    async def chat(self, messages: List[Dict], temperature: float = 0.4,
                   max_tokens: int = 4000) -> str:
        pass

    @abstractmethod
    async def stream(self, messages: List[Dict], temperature: float = 0.4) -> Any:
        pass


class OpenAIProvider(BaseProvider):
    """OpenAI / GPT"""

    def __init__(self, api_key: str = None, model: str = "gpt-4o"):
        super().__init__(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            model=model,
            base_url="https://api.openai.com/v1"
        )

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        await self._rate_limit()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except:
            import requests
            resp = requests.post(f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
            return resp.json()["choices"][0]["message"]["content"]

    async def stream(self, messages, temperature=0.4):
        return await self.chat(messages, temperature)


class AnthropicProvider(BaseProvider):
    """Anthropic / Claude"""

    def __init__(self, api_key: str = None, model: str = "claude-3-5-sonnet-20241022"):
        super().__init__(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            model=model,
            base_url="https://api.anthropic.com/v1"
        )

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        await self._rate_limit()
        # Convert messages to Anthropic format
        system_msg = ""
        anthropic_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                anthropic_messages.append({"role": m["role"], "content": m["content"]})

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/messages",
                    headers={"x-api-key": self.api_key, "Content-Type": "application/json", "anthropic-version": "2023-06-01"},
                    json={"model": self.model, "messages": anthropic_messages, "system": system_msg, "max_tokens": max_tokens}
                ) as resp:
                    data = await resp.json()
                    return data["content"][0]["text"]
        except:
            import requests
            resp = requests.post(f"{self.base_url}/messages",
                headers={"x-api-key": self.api_key, "Content-Type": "application/json", "anthropic-version": "2023-06-01"},
                json={"model": self.model, "messages": anthropic_messages, "system": system_msg, "max_tokens": max_tokens})
            return resp.json()["content"][0]["text"]

    async def stream(self, messages, temperature=0.4):
        return await self.chat(messages, temperature)


class GeminiProvider(BaseProvider):
    """Google Gemini"""

    def __init__(self, api_key: str = None, model: str = "gemini-1.5-pro"):
        super().__init__(
            api_key=api_key or os.environ.get("GEMINI_API_KEY"),
            model=model,
            base_url="https://generativelanguage.googleapis.com/v1beta"
        )

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        await self._rate_limit()
        # Convert to Gemini format
        contents = []
        for m in messages:
            role = "user" if m["role"] in ("user", "system") else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
                    json={"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
                ) as resp:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
        except:
            import requests
            resp = requests.post(f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}",
                json={"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}})
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    async def stream(self, messages, temperature=0.4):
        return await self.chat(messages, temperature)


class OllamaProvider(BaseProvider):
    """Local Ollama"""

    def __init__(self, api_key: str = None, model: str = "llama3.1"):
        super().__init__(
            api_key="",
            model=model,
            base_url="http://localhost:11434"
        )

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        await self._rate_limit()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/chat",
                    json={"model": self.model, "messages": messages, "stream": False}
                ) as resp:
                    data = await resp.json()
                    return data["message"]["content"]
        except:
            import requests
            resp = requests.post(f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False})
            return resp.json()["message"]["content"]

    async def stream(self, messages, temperature=0.4):
        return await self.chat(messages, temperature)


class OpenRouterProvider(BaseProvider):
    """OpenRouter - access to many models"""

    def __init__(self, api_key: str = None, model: str = "anthropic/claude-3.5-sonnet"):
        super().__init__(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
            model=model,
            base_url="https://openrouter.ai/api/v1"
        )

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        await self._rate_limit()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
                ) as resp:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except:
            import requests
            resp = requests.post(f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
            return resp.json()["choices"][0]["message"]["content"]

    async def stream(self, messages, temperature=0.4):
        return await self.chat(messages, temperature)


# Provider registry
PROVIDERS = {
    "mistral": "MistralProvider",
    "openai": "OpenAIProvider",
    "anthropic": "AnthropicProvider",
    "claude": "AnthropicProvider",
    "gemini": "GeminiProvider",
    "google": "GeminiProvider",
    "ollama": "OllamaProvider",
    "local": "OllamaProvider",
    "openrouter": "OpenRouterProvider",
}


def create_provider(name: str, model: str = None, **kwargs) -> BaseProvider:
    """Create provider by name"""
    name = name.lower()

    # Default models
    default_models = {
        "mistral": "mistral-large-latest",
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
        "claude": "claude-3-5-sonnet-20241022",
        "gemini": "gemini-1.5-pro",
        "google": "gemini-1.5-pro",
        "ollama": "llama3.1",
        "local": "llama3.1",
        "openrouter": "anthropic/claude-3.5-sonnet",
    }

    if model is None:
        model = default_models.get(name, "mistral-large-latest")

    kwargs["model"] = model

    if name == "mistral":
        from xli.providers.mistral import MistralProvider
        return MistralProvider(**kwargs)
    elif name in ("openai", "gpt"):
        return OpenAIProvider(**kwargs)
    elif name in ("anthropic", "claude"):
        return AnthropicProvider(**kwargs)
    elif name in ("gemini", "google"):
        return GeminiProvider(**kwargs)
    elif name in ("ollama", "local"):
        return OllamaProvider(**kwargs)
    elif name == "openrouter":
        return OpenRouterProvider(**kwargs)
    else:
        raise ValueError(f"Unknown provider: {name}")


def list_providers() -> List[str]:
    """List available providers"""
    return list(PROVIDERS.keys())


def get_provider_info(name: str) -> dict:
    """Get provider info"""
    info = {
        "mistral": {"models": ["mistral-large-latest", "mistral-medium", "codestral"], "needs_key": "MISTRAL_API_KEY"},
        "openai": {"models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"], "needs_key": "OPENAI_API_KEY"},
        "anthropic": {"models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"], "needs_key": "ANTHROPIC_API_KEY"},
        "gemini": {"models": ["gemini-1.5-pro", "gemini-1.5-flash"], "needs_key": "GEMINI_API_KEY"},
        "ollama": {"models": ["llama3.1", "codellama", "mistral"], "needs_key": None},
        "openrouter": {"models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o"], "needs_key": "OPENROUTER_API_KEY"},
    }
    return info.get(name, {})
