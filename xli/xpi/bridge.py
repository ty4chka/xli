#!/usr/bin/env python3
"""
XPI Bridge v4 — Python ↔ Lua RPC для Neovim
"""

import json
from typing import Any, Dict, Optional, Callable

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.xpi.bridge")


class XpiBridge:
    """RPC bridge between Python and Lua in Neovim"""

    def __init__(self, nvim=None):
        self.nvim = nvim
        self._handlers: Dict[str, Callable] = {}
        logger.log_structured("INFO", "xpi.bridge", "Bridge initialized")

    def register_handler(self, method: str, handler: Callable):
        """Register Python handler for Lua calls"""
        self._handlers[method] = handler
        logger.log_structured("DEBUG", "xpi.bridge", f"Handler: {method}")

    def call_lua(self, code: str) -> Any:
        """Execute Lua code in Neovim"""
        if not self.nvim:
            logger.log_structured("WARN", "xpi.bridge", "No nvim connection")
            return None

        try:
            return self.nvim.exec_lua(code)
        except Exception as e:
            logger.log_error("xpi.bridge", "Lua call failed", exc=e)
            return None

    def notify_lua(self, event: str, data: Dict):
        """Send notification to Lua"""
        if not self.nvim:
            return

        try:
            payload = json.dumps(data)
            self.nvim.command(f'lua require("xli").handle_rpc("{event}", {payload})')
        except Exception as e:
            logger.log_error("xpi.bridge", "Notify failed", exc=e)

    def handle_python_call(self, method: str, *args, **kwargs) -> Any:
        """Handle call from Lua"""
        if method in self._handlers:
            try:
                return self._handlers[method](*args, **kwargs)
            except Exception as e:
                logger.log_error("xpi.bridge", f"Handler {method} failed", exc=e)

        return None
