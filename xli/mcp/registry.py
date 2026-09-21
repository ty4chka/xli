#!/usr/bin/env python3
"""
XLI MCP Registry — server definitions and management
"""

from typing import Any
from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.mcp.registry")

# Built-in server definitions
SERVERS = {
    "knowledge": {
        "description": "Code knowledge base search",
        "tools": ["search", "query"],
        "enabled": True,
    },
    "architecture": {
        "description": "Dependency graph and architecture analysis",
        "tools": ["analyze_deps", "get_graph"],
        "enabled": True,
    },
    "debugger": {
        "description": "Debug and error analysis",
        "tools": ["analyze_error", "suggest_fix"],
        "enabled": True,
    },
    "auto_tester": {
        "description": "Automated testing",
        "tools": ["run_tests", "check_coverage"],
        "enabled": True,
    },
    "code_formatter": {
        "description": "Code formatting with black/ruff",
        "tools": ["format", "lint"],
        "enabled": True,
    },
    "security_scanner": {
        "description": "Security vulnerability scanning",
        "tools": ["scan", "check_dependencies"],
        "enabled": True,
    },
    "file_manager": {
        "description": "File operations",
        "tools": ["read", "write", "list", "grep"],
        "enabled": True,
    },
    "git_mcp": {
        "description": "Git operations",
        "tools": ["status", "diff", "commit", "branch"],
        "enabled": True,
    },
    "shell_helper": {
        "description": "Shell command assistance",
        "tools": ["suggest", "validate"],
        "enabled": True,
    },
    "env_manager": {
        "description": "Environment variable management",
        "tools": ["read", "write", "validate"],
        "enabled": True,
    },
    "db_client": {
        "description": "Database client",
        "tools": ["query", "schema"],
        "enabled": False,
    },
    "http_client": {
        "description": "HTTP client for API testing",
        "tools": ["request", "test"],
        "enabled": False,
    },
    "doc_generator": {
        "description": "Documentation generation",
        "tools": ["generate", "build"],
        "enabled": False,
    },
    "refactor": {
        "description": "Code refactoring",
        "tools": ["suggest", "apply"],
        "enabled": True,
    },
    "archaeologist": {
        "description": "Git history analysis",
        "tools": ["blame", "history"],
        "enabled": True,
    },
    "prompt": {
        "description": "Prompt management",
        "tools": ["version", "optimize"],
        "enabled": True,
    },
    "package_monitor": {
        "description": "Package vulnerability monitoring",
        "tools": ["check", "audit"],
        "enabled": True,
    },
    "lsp": {
        # Referenced by XliAgent._get_mcp_tools_prompt() (chain.py) but had
        # no entry here — is_enabled("lsp") was silently always False since
        # MCPRegistry.is_enabled() treats a missing name as disabled, and no
        # actual LSP integration exists in xli/skills/ yet. Registered here
        # (disabled) so that's an explicit, documented fact instead of a
        # lookup on a name that was never defined.
        "description": "Language server protocol (diagnostics, hover, goto) — not yet implemented",
        "tools": ["diagnostics", "hover", "goto_definition"],
        "enabled": False,
    },
}


class MCPRegistry:
    """MCP server registry"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: Any = None):
        if self._initialized:
            return
        self._initialized = True
        # Deep-ish copy: the module-level SERVERS must not be mutated by
        # enable()/disable(), or a disabled server would stay disabled for
        # every registry built later in the same process.
        self.servers = {name: dict(info) for name, info in SERVERS.items()}
        self._config = config
        self._apply_config(config if config is not None else self._load_config())
        logger.log_structured("INFO", "mcp.registry", f"Loaded {len(self.servers)} servers")

    @staticmethod
    def _load_config():
        try:
            from xli.manager.config import get_config

            return get_config()
        except Exception:  # noqa: BLE001 - a broken config must not stop the registry
            return None

    def _apply_config(self, config: Any) -> None:
        """Let the config override the built-in enabled flags.

        Without this, enable()/disable() only mutated an in-memory dict, so
        `mcp.set_enabled` over RPC reported success and the change evaporated
        on the next start.
        """
        if config is None:
            return
        try:
            disabled = set(config.get("mcp.disabled") or [])
            forced_on = set(config.get("mcp.enabled_servers") or [])
        except Exception:  # noqa: BLE001
            return
        for name in self.servers:
            if name in disabled:
                self.servers[name]["enabled"] = False
            elif name in forced_on:
                self.servers[name]["enabled"] = True

    def _persist(self, name: str, enabled: bool) -> bool:
        """Record the change in the config so it survives a restart."""
        config = self._config if self._config is not None else self._load_config()
        if config is None:
            return False
        try:
            disabled = list(config.get("mcp.disabled") or [])
            forced_on = list(config.get("mcp.enabled_servers") or [])
            if enabled:
                disabled = [n for n in disabled if n != name]
                if SERVERS.get(name, {}).get("enabled") is not True and name not in forced_on:
                    forced_on.append(name)
            else:
                forced_on = [n for n in forced_on if n != name]
                if name not in disabled:
                    disabled.append(name)
            config.set("mcp.disabled", disabled)
            config.set("mcp.enabled_servers", forced_on)
            config.save()
            return True
        except Exception as exc:  # noqa: BLE001 - report, do not crash the caller
            logger.log_error("mcp.registry", f"could not persist {name}", exc=exc)
            return False

    def get_server(self, name: str) -> dict | None:
        """Get server definition"""
        return self.servers.get(name)

    def is_enabled(self, name: str) -> bool:
        """Check if server is enabled"""
        server = self.servers.get(name)
        return server.get("enabled", False) if server else False

    def list_enabled(self) -> list[str]:
        """List enabled server names"""
        return [name for name, info in self.servers.items() if info.get("enabled", False)]

    def list_all(self) -> list[dict]:
        """List all servers with status"""
        return [
            {"name": name, "description": info.get("description", ""), "enabled": info.get("enabled", False)}
            for name, info in self.servers.items()
        ]

    def enable(self, name: str) -> bool:
        """Enable a server and persist the choice. Returns False if unknown."""
        if name not in self.servers:
            return False
        self.servers[name]["enabled"] = True
        return self._persist(name, True)

    def disable(self, name: str) -> bool:
        """Disable a server and persist the choice. Returns False if unknown."""
        if name not in self.servers:
            return False
        self.servers[name]["enabled"] = False
        return self._persist(name, False)


def get_registry() -> MCPRegistry:
    """Get singleton MCPRegistry"""
    return MCPRegistry()


def get_available_servers() -> dict[str, Any]:
    """Get available servers (for env.py)"""
    registry = get_registry()
    return {name: info for name, info in registry.servers.items() if info.get("enabled", False)}
