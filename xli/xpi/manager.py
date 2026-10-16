#!/usr/bin/env python3
"""
XPI Manager — loads, instantiates and dispatches internal plugins.

XPI ("XLI Plugin Interface") is xli's internal plugin system: small Python
packages dropped into `~/.xli/xpi/<name>/`, each with a `manifest.json` and a
class deriving from `xli.xpi.base.XpiPlugin`. Unlike MCP servers (separate
processes, remote) or skills (markdown guidance), an XPI plugin runs in-process
and gets lifecycle hooks — it can watch the agent work, keep state across UI
platforms, and extend what the frontends show.

Layout
------
    ~/.xli/xpi/
      my-plugin/
        manifest.json     {"name": "my-plugin", "version": "1.0", "main": "plugin.py"}
        plugin.py         class MyPlugin(XpiPlugin): ...

Isolation rules this file follows:

  * **One bad plugin never takes down the others.** Loading and hook dispatch
    are wrapped per plugin; failures are collected and reported, not raised.
  * **A hook that crashes is not silently swallowed.** The old `call_hook`
    returned None both for "no such hook" and "hook raised", so a caller could
    not tell them apart. `dispatch` returns a report instead.
  * **The singleton can be reset**, so tests (and `xli plugins reload`) can
    pick up changes without restarting the process.
"""

from __future__ import annotations

import importlib.util
import json
import inspect
import re
import weakref
from copy import deepcopy
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from xli.paths import xli_path
from xli.core.logger import StructuredLogger
from xli.xpi.base import XpiPlugin
from xli.xpi.registry import XpiRegistry

logger = StructuredLogger("xli.xpi.manager")

XPI_DIR = xli_path("xpi")

#: Hooks the lifecycle knows about. A plugin may implement any subset.
KNOWN_HOOKS = (
    "on_load",
    "on_agent_start",
    "on_tool_call",
    "on_tool_result",
    "on_agent_end",
    "on_tui_mount",
    "on_nvim_attach",
    "on_headless_start",
    "on_unload",
)


@dataclass
class XpiInfo:
    """Metadata for one loaded plugin."""

    name: str
    version: str
    path: Path
    enabled: bool = True
    platform: str = "all"
    entry: str = ""
    loaded_at: datetime | None = None
    error: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    author: str = ""
    description: str = ""
    license: str = ""
    homepage: str = ""
    api_version: int = 1
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "path": str(self.path),
            "enabled": self.enabled,
            "platform": self.platform,
            "entry": self.entry,
            "loaded_at": self.loaded_at.isoformat(timespec="seconds") if self.loaded_at else None,
            "error": self.error,
            "hooks": sorted(self.hooks),
            "author": self.author,
            "description": self.description,
            "license": self.license,
            "homepage": self.homepage,
            "api_version": self.api_version,
            "tools": self.tools,
        }


@dataclass
class DispatchReport:
    """What happened when a hook was broadcast."""

    hook: str
    called: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook,
            "called": self.called,
            "errors": self.errors,
            "results": self.results,
        }


class XpiManager:
    """Discovers, loads and drives XPI plugins."""

    _instance: XpiManager | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> XpiManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, root: Path | None = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.root = Path(root) if root else XPI_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.plugins: dict[str, XpiInfo] = {}
        self.instances: dict[str, XpiPlugin] = {}
        self._modules: dict[str, Any] = {}
        self.registry = XpiRegistry()
        self._registries = weakref.WeakSet()
        self._tools = {}

        self.load_all()
        logger.log_structured("INFO", "xpi.manager", f"{len(self.plugins)} plugin(s) available")

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton so the next call re-scans from disk."""
        if cls._instance is not None:
            cls._instance.unload_all()
        cls._instance = None

    # ------------------------------------------------------------- discovery
    def load_all(self) -> None:
        """Load every plugin directory that has a manifest."""
        if not self.root.is_dir():
            return
        for plugin_dir in sorted(self.root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                logger.log_structured(
                    "WARN", "xpi.manager", f"{plugin_dir.name} has no manifest.json — skipped"
                )
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.log_error("xpi.manager", f"bad manifest in {plugin_dir.name}", exc=exc)
                self.plugins[plugin_dir.name] = XpiInfo(
                    name=plugin_dir.name, version="?", path=plugin_dir, error=f"bad manifest: {exc}"
                )
                continue
            self._load_plugin(plugin_dir, manifest)

    def _load_plugin(self, path: Path, manifest: dict) -> None:
        try:
            self.validate_manifest(path, manifest)
        except (ValueError, TypeError) as exc:
            self.plugins[path.name] = XpiInfo(
                name=path.name, version="?", path=path, error=f"bad manifest: {exc}")
            return
        name = str(manifest.get("name") or path.name)
        if name in self.instances:
            return  # repeated discovery must not duplicate lifecycle calls
        info = XpiInfo(
            name=name,
            version=str(manifest.get("version", "0.1.0")),
            path=path,
            enabled=bool(manifest.get("enabled", True)),
            platform=str(manifest.get("platform", "all")),
            entry=str(manifest.get("entry", "")),
            loaded_at=datetime.now(),
            hooks=dict(manifest.get("hooks", {})),
            author=manifest.get("author", ""),
            description=manifest.get("description", ""),
            license=manifest.get("license", ""),
            homepage=manifest.get("homepage", ""),
            api_version=manifest.get("api_version", 1),
        )
        self.plugins[name] = info

        if not info.enabled:
            logger.log_structured("INFO", "xpi.manager", f"{name} is disabled — not loaded")
            return

        main_file = path / str(manifest.get("main", f"{name}.py"))
        if not main_file.exists():
            main_file = path / "__init__.py"
        if not main_file.exists():
            info.error = f"entry point not found (looked for {manifest.get('main', name + '.py')})"
            logger.log_structured("WARN", "xpi.manager", f"{name}: {info.error}")
            return

        try:
            module = self._import_module(name, main_file)
        except Exception as exc:  # noqa: BLE001 - one plugin must not stop the rest
            info.error = f"import failed: {type(exc).__name__}: {exc}"
            logger.log_error("xpi.manager", f"{name}: {info.error}")
            self._teardown(name)
            return

        self._modules[name] = module

        try:
            instance = self._instantiate(name, module, info.entry)
        except Exception as exc:  # noqa: BLE001
            info.error = f"could not instantiate: {type(exc).__name__}: {exc}"
            logger.log_error("xpi.manager", f"{name}: {info.error}")
            self._teardown(name)
            return

        self.instances[name] = instance
        from xli.xpi.sdk import PluginContext
        instance.context = PluginContext(name, deepcopy(manifest.get("config", {})))
        try:
            self._sync_result(instance.on_load({
                "plugin": name, "config": deepcopy(instance.context.config),
            }))
            info.hooks = {hook: True for hook in KNOWN_HOOKS
                          if getattr(type(instance), hook) is not getattr(XpiPlugin, hook)}
            from xli.tools.base import Tool
            exported = []
            seen = set()
            for obj in instance.tools():
                if not isinstance(obj, Tool) or not re.fullmatch(r"[A-Za-z_][\w-]*", obj.name):
                    raise ValueError("tools() must return Tools with simple names")
                if obj.name in seen:
                    raise ValueError(f"duplicate tool {obj.name}")
                seen.add(obj.name)
                # Copy the spec, not the callback's owning plugin instance.
                from copy import copy
                obj = copy(obj)
                obj.spec = deepcopy(obj.spec)
                obj.spec.name = f"{name}.{obj.name}"
                exported.append(obj)
            self._tools[name] = exported
            info.tools = [obj.name for obj in exported]
            for registry in self._registries:
                self._install_tools(name, registry)
        except Exception as exc:
            info.error = f"setup failed: {type(exc).__name__}: {exc}"
            self._teardown(name)
            return
        self.registry.register(name, info.version, info.platform)
        logger.log_structured("INFO", "xpi.manager", f"loaded {name} v{info.version}")

    @staticmethod
    def validate_manifest(path: Path, manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        name = manifest.get("name", path.name)
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise ValueError("invalid plugin name")
        if name != path.name:
            raise ValueError("plugin name must match its directory")
        if type(manifest.get("api_version", 1)) is not int or manifest.get("api_version", 1) != 1:
            raise ValueError("unsupported api_version (supported: 1)")
        if type(manifest.get("enabled", True)) is not bool:
            raise ValueError("enabled must be a boolean")
        if manifest.get("platform", "all") not in ("all", "headless", "tui", "nvim"):
            raise ValueError("invalid platform")
        for key in ("version", "main", "entry", "author", "description", "license", "homepage"):
            if key in manifest and not isinstance(manifest[key], str):
                raise ValueError(f"{key} must be a string")
        for key in ("hooks", "config"):
            if key in manifest and not isinstance(manifest[key], dict):
                raise ValueError(f"{key} must be an object")
        main = (path / manifest.get("main", f"{name}.py")).resolve()
        if not main.is_relative_to(path.resolve()):
            raise ValueError("main must stay inside the plugin directory")
        if not (path / "__init__.py").resolve().is_relative_to(path.resolve()):
            raise ValueError("package entry must stay inside the plugin directory")

    @staticmethod
    def _sync_result(result):
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("XPI lifecycle hooks must be synchronous; tools may be async")
        return result

    def attach_registry(self, registry) -> None:
        """Install namespaced tools; disable/reload removes them from every registry."""
        self._registries.add(registry)
        registry.plugins = self
        for name in list(self._tools):
            try:
                self._install_tools(name, registry)
            except Exception as exc:
                self.plugins[name].error = f"tool registration failed: {exc}"
                self._teardown(name)

    def _install_tools(self, name, registry):
        info = self.plugins[name]
        platform = getattr(registry, "plugin_platform", "headless")
        if info.platform not in ("all", platform):
            return
        for obj in self._tools.get(name, []):
            if registry.get(obj.name) is obj:
                continue
            registry.register(obj)  # never overwrite a builtin or another extension

    def _import_module(self, name: str, path: Path) -> Any:
        """Import a plugin file under a stable, unique module name."""
        qualified = f"xli_xpi_plugin_{name.encode().hex()}"

        # Drop any cached bytecode first. CPython validates a .pyc by source
        # mtime *in whole seconds* and size, so editing a plugin and reloading
        # within the same second — or making an equal-length edit — leaves the
        # cache looking valid and the old code runs. Hot reload has to read the
        # source it was asked to read.
        try:
            cached = importlib.util.cache_from_source(str(path))
            Path(cached).unlink(missing_ok=True)
        except (NotImplementedError, OSError):
            pass
        importlib.invalidate_caches()

        spec = importlib.util.spec_from_file_location(
            qualified, str(path), submodule_search_locations=[str(path.parent)])
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot build an import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        # Register before exec so a plugin importing itself, or two plugins
        # sharing a helper name, do not clobber each other mid-import.
        sys.modules[qualified] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(qualified, None)
            raise
        return module

    @staticmethod
    def _instantiate(name: str, module: Any, entry: str) -> XpiPlugin:
        """Find and construct the plugin class.

        `entry` names the class explicitly; without it the module must expose
        exactly one XpiPlugin subclass, so a stray helper class cannot be
        picked up by accident.
        """
        if entry:
            cls = getattr(module, entry, None)
            if cls is None:
                raise AttributeError(f"{name}: no class named {entry!r}")
        else:
            candidates = [
                value
                for key, value in vars(module).items()
                if isinstance(value, type)
                and issubclass(value, XpiPlugin)
                and value is not XpiPlugin
                and value.__module__ == module.__name__
            ]
            if not candidates:
                raise AttributeError(f"{name}: no XpiPlugin subclass found")
            if len(candidates) > 1:
                names = ", ".join(sorted(c.__name__ for c in candidates))
                raise AttributeError(
                    f"{name}: multiple XpiPlugin subclasses ({names}) — set "
                    f'"entry" in manifest.json'
                )
            cls = candidates[0]

        instance = cls()
        if not isinstance(instance, XpiPlugin):
            raise TypeError(f"{name}: {cls.__name__} is not an XpiPlugin")
        return instance

    # --------------------------------------------------------------- lifecycle
    def dispatch(self, hook: str, platform: str | None = None, **context: Any) -> DispatchReport:
        """Call `hook` on every enabled plugin whose platform matches.

        Returns a report rather than raising: a plugin that throws is recorded
        and the others still run.
        """
        report = DispatchReport(hook=hook)
        if hook not in KNOWN_HOOKS:
            return report

        for name, plugin in sorted(self.instances.items()):
            info = self.plugins.get(name)
            if info is not None and not info.enabled:
                continue
            if platform and info is not None and info.platform not in (platform, "all"):
                continue

            handler = getattr(plugin, hook, None)
            if not callable(handler):
                continue

            report.called.append(name)
            try:
                result = self._sync_result(handler(deepcopy(context)))
                if result is not None:
                    report.results[name] = result
            except Exception as exc:  # noqa: BLE001 - isolate the failure
                report.errors.append(
                    {"plugin": name, "error": f"{type(exc).__name__}: {exc}"}
                )
                logger.log_error("xpi.manager", f"{name}.{hook} failed", exc=exc)

        return report

    def call_hook(self, name: str, hook: str, *args: Any, **kwargs: Any) -> Any:
        """Call one hook on one plugin. Kept for compatibility."""
        plugin = self.instances.get(name)
        if plugin is None:
            return None
        handler = getattr(plugin, hook, None)
        if not callable(handler):
            return None
        try:
            return self._sync_result(handler(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            logger.log_error("xpi.manager", f"{name}.{hook} failed", exc=exc)
            return None

    # ----------------------------------------------------------------- control
    def get(self, name: str) -> XpiInfo | None:
        return self.plugins.get(name)

    def instance(self, name: str) -> XpiPlugin | None:
        return self.instances.get(name)

    def list_plugins(self) -> list[dict[str, Any]]:
        return [info.to_dict() for info in self.plugins.values()]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a plugin and rewrite its manifest accordingly."""
        info = self.plugins.get(name)
        if info is None or type(enabled) is not bool:
            return False

        manifest_path = info.path / "manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.validate_manifest(info.path, manifest)
            manifest["enabled"] = enabled
            temporary.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            temporary.replace(manifest_path)
        except (ValueError, TypeError, OSError) as exc:
            info.error = f"cannot update manifest: {exc}"
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        info.enabled = enabled
        if enabled:
            return self.reload(name)
        else:
            self._teardown(name)
        return True

    def reload(self, name: str) -> bool:
        """Re-import a plugin from disk (hot reload)."""
        info = self.plugins.get(name)
        if info is None:
            return False

        self._teardown(name)

        manifest_path = info.path / "manifest.json"
        if not manifest_path.exists():
            info.error = "reload failed: manifest.json not found"
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            info.error = f"reload failed: bad manifest: {exc}"
            logger.log_error("xpi.manager", f"reload {name}: bad manifest", exc=exc)
            return False

        before = len(self.instances)
        self._load_plugin(info.path, manifest)
        reloaded = name in self.instances
        logger.log_structured(
            "INFO", "xpi.manager", f"reloaded {name}" if reloaded else f"reload failed: {name}"
        )
        return reloaded or len(self.instances) > before

    def _teardown(self, name: str) -> None:
        """Unload a plugin: on_unload hook, then drop every reference."""
        for obj in self._tools.pop(name, []):
            for registry in self._registries:
                if registry.get(obj.name) is obj:
                    registry.unregister(obj.name)
        plugin = self.instances.pop(name, None)
        if plugin is not None:
            try:
                self._sync_result(plugin.on_unload({}))
            except Exception as exc:  # noqa: BLE001
                logger.log_error("xpi.manager", f"{name}.on_unload failed", exc=exc)

        self.registry.unregister(name)
        qualified = f"xli_xpi_plugin_{name.encode().hex()}"
        # pop, not del: the module may never have been registered.
        for module_name in list(sys.modules):
            if module_name == qualified or module_name.startswith(qualified + "."):
                module = sys.modules.pop(module_name, None)
                cached = getattr(module, "__cached__", None)
                info = self.plugins.get(name)
                if cached and info and Path(cached).resolve().is_relative_to(info.path.resolve()):
                    try:
                        Path(cached).unlink(missing_ok=True)
                    except OSError:
                        pass
        self._modules.pop(name, None)

    def unload_all(self) -> None:
        for name in list(self.instances):
            self._teardown(name)


def get_xpi_manager(root: Path | None = None) -> XpiManager:
    """Process-wide XpiManager."""
    return XpiManager(root)
