#!/usr/bin/env python3
"""
XLI Fake Provider — deterministic, offline stand-in for AbstractProvider.

Use in tests via the `fake_provider` fixture in tests/conftest.py so the test
suite doesn't need real MISTRAL_API_KEY/OPENAI_API_KEY/etc. Responses are
scripted: pass a list of strings (or a callable) and they're returned in
order on each .chat() call.
"""

from typing import Any, Dict, List, Optional, Union, Callable

from xli.providers.base import AbstractProvider


class FakeProvider(AbstractProvider):
    """In-memory provider. No network calls, no API key required."""

    def __init__(
        self,
        responses: Optional[List[str]] = None,
        response_fn: Optional[Callable[[List[Dict]], str]] = None,
        model: str = "fake-model",
    ):
        super().__init__(api_key="fake-key", model=model)
        self._responses = list(responses or ["OK"])
        self._response_fn = response_fn
        self.calls: List[List[Dict]] = []

    def _next_response(self, messages: List[Dict]) -> str:
        self.calls.append(messages)
        if self._response_fn is not None:
            return self._response_fn(messages)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    async def chat(self, messages: List[Dict], temperature: float = 0.4,
                    max_tokens: int = 4000) -> str:
        return self._next_response(messages)

    async def stream(self, messages: List[Dict], temperature: float = 0.4) -> Any:
        text = self._next_response(messages)
        async def _gen():
            yield text
        return _gen()

    async def embed(self, text: str) -> List[float]:
        # Deterministic fake embedding, fine for tests that just check shape/type.
        return [float(len(text) % 7)] * 8
