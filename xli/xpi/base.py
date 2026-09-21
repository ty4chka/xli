#!/usr/bin/env python3
"""
XPI Plugin Base v4 — Unified update, platform-specific hooks
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.xpi.base")


class XpiPlugin(ABC):
    """Base class for XPI plugins"""

    def __init__(self, name: str, version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.platform = "all"  # Override in subclass
        self.logger = StructuredLogger(f"xpi.{name}")

    @abstractmethod
    def update(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Unified update — called on all platforms"""
        pass

    def on_tui_mount(self, app):
        """Called when TUI mounts"""
        pass

    def on_nvim_attach(self, nvim):
        """Called when Neovim attaches"""
        pass

    def on_headless_start(self, args):
        """Called in headless mode"""
        pass

    def get_platform_hook(self, platform: str):
        """Get platform-specific hook"""
        hooks = {
            "tui": self.on_tui_mount,
            "nvim": self.on_nvim_attach,
            "headless": self.on_headless_start,
        }
        return hooks.get(platform)

    def initialize(self, platform: str, **kwargs):
        """Initialize for specific platform"""
        hook = self.get_platform_hook(platform)
        if hook:
            try:
                hook(**kwargs)
                self.logger.log_structured("INFO", f"xpi.{self.name}", 
                                          f"Initialized for {platform}")
            except Exception as e:
                self.logger.log_error(f"xpi.{self.name}", 
                                     f"Init failed for {platform}", exc=e)
