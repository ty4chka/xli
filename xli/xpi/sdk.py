"""Small public SDK; no frontend or curses imports required."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from xli.tools.base import Param, Tool, ToolResult, tool
from xli.xpi.state import XpiState


@dataclass
class PluginContext:
    """Configuration and persistent, plugin-namespaced JSON state.

    Available as ``self.context`` before on_load. XPI code is trusted Python,
    not sandboxed; namespacing prevents accidental collisions, not attacks.
    """

    name: str
    config: dict[str, Any] = field(default_factory=dict)

    def get_state(self, key: str, default: Any = None) -> Any:
        return deepcopy(XpiState().get(f"plugin:{self.name}:{key}", default))

    def set_state(self, key: str, value: Any) -> None:
        import json
        json.dumps(value, allow_nan=False)
        XpiState().set(f"plugin:{self.name}:{key}", deepcopy(value))

    def delete_state(self, key: str) -> bool:
        return XpiState().delete(f"plugin:{self.name}:{key}")


__all__ = ["PluginContext", "Param", "Tool", "ToolResult", "tool"]
