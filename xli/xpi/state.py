#!/usr/bin/env python3
"""
XPI State v4 — Shared state between TUI/Nvim/Headless
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.xpi.state")

STATE_FILE = Path.home() / ".xli" / "xpi_state.json"


class XpiState:
    """Shared state across all UI platforms"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._state: Dict[str, Any] = {}
        self._load()
        logger.log_structured("INFO", "xpi.state", "State initialized")

    def _load(self):
        """Load persisted state"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    self._state = json.load(f)
            except Exception as e:
                logger.log_error("xpi.state", "Load failed", exc=e)

    def _save(self):
        """Persist state"""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.log_error("xpi.state", "Save failed", exc=e)

    def get(self, key: str, default: Any = None) -> Any:
        """Get state value"""
        return self._state.get(key, default)

    def set(self, key: str, value: Any):
        """Set state value"""
        self._state[key] = value
        self._save()
        logger.log_structured("DEBUG", "xpi.state", f"Set: {key}")

    def delete(self, key: str):
        """Delete state key"""
        if key in self._state:
            del self._state[key]
            self._save()

    def clear(self):
        """Clear all state"""
        self._state.clear()
        self._save()
        logger.log_structured("INFO", "xpi.state", "State cleared")

    def all(self) -> Dict[str, Any]:
        """Get all state"""
        return dict(self._state)


def get_xpi_state() -> XpiState:
    """Get singleton XpiState"""
    return XpiState()
