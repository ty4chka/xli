#!/usr/bin/env python3
"""
XLI Mistral Provider v4.2 — RESILIENT: skip on 429, don't kill step
"""

import os
import asyncio
import time
from typing import List, Dict, Optional, Any

from xli.providers.base import AbstractProvider
from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.providers.mistral")


class MistralProvider(AbstractProvider):
    """Mistral AI provider with resilience"""

    def __init__(self, api_key: str = None, model: str = "mistral-large-latest"):
        super().__init__(api_key=api_key, model=model)
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        self.base_url = "https://api.mistral.ai/v1"
        self.last_request_time = 0
        self.min_delay = 4.0  # INCREASED for free tier
        self.consecutive_errors = 0
        self.total_requests = 0

        if not self.api_key:
            raise ValueError("Mistral API key required. Set MISTRAL_API_KEY env var.")

    async def _rate_limit(self):
        """Rate limiting with jitter"""
        now = time.time()
        elapsed = now - self.last_request_time
        delay = self.min_delay + (self.consecutive_errors * 3)
        if elapsed < delay:
            wait = delay - elapsed
            logger.log_structured("DEBUG", "mistral", f"Wait: {wait:.1f}s")
            await asyncio.sleep(wait)
        self.last_request_time = time.time()
        self.total_requests += 1

    async def chat(self, messages: List[Dict], temperature: float = 0.4,
                   max_tokens: int = 4000) -> str:
        """Chat with smart fallback on 429"""
        await self._rate_limit()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self._chat_once(messages, temperature, max_tokens)
                self.consecutive_errors = 0
                return result
            except RuntimeError as e:
                if "429" in str(e):
                    self.consecutive_errors += 1
                    if attempt < max_retries - 1:
                        wait = 8 * (2 ** attempt)  # 8s, 16s, 32s
                        logger.log_structured("WARN", "mistral",
                                             f"429 (attempt {attempt+1}), wait {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        # FINAL: return error as content instead of crashing
                        logger.log_structured("ERROR", "mistral", "429: returning error stub")
                        return "[ERROR: Rate limit exceeded (429). Please wait a minute and retry.]"
                else:
                    raise
            except Exception as e:
                logger.log_error("mistral", f"Error (attempt {attempt+1})", exc=e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    return f"[ERROR: {str(e)}]"

    async def _chat_once(self, messages: List[Dict], temperature: float = 0.4,
                         max_tokens: int = 4000) -> str:
        """Single chat attempt"""
        try:
            import aiohttp
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status == 429:
                        text = await resp.text()
                        raise RuntimeError(f"Mistral API 429: {text[:200]}")
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Mistral API error {resp.status}: {text[:200]}")
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except ImportError:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=payload, timeout=120
            )
            if resp.status_code == 429:
                raise RuntimeError(f"Mistral API 429: {resp.text[:200]}")
            if resp.status_code != 200:
                raise RuntimeError(f"Mistral API error {resp.status_code}")
            return resp.json()["choices"][0]["message"]["content"]

    async def stream(self, messages: List[Dict], temperature: float = 0.4) -> Any:
        return await self.chat(messages, temperature)

    async def embed(self, text: str) -> List[float]:
        try:
            import aiohttp
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {"model": "mistral-embed", "input": text}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/embeddings",
                    headers=headers, json=payload
                ) as resp:
                    data = await resp.json()
                    return data["data"][0]["embedding"]
        except Exception as e:
            logger.log_error("mistral", "Embed failed", exc=e)
            return []
