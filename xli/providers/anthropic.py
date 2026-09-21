#!/usr/bin/env python3
"""Anthropic Provider — placeholder fallback"""
from xli.providers.base import AbstractProvider
from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.providers.anthropic")

class AnthropicProvider(AbstractProvider):
    def __init__(self, api_key: str = None, model: str = "claude-3-opus-20240229"):
        super().__init__(api_key=api_key, model=model)
        self.api_key = api_key

    async def chat(self, messages, temperature=0.4, max_tokens=4000):
        raise NotImplementedError("Anthropic provider not yet implemented")

    async def stream(self, messages, temperature=0.4):
        raise NotImplementedError("Anthropic streaming not yet implemented")

    async def embed(self, text):
        raise NotImplementedError("Anthropic embed not yet implemented")
