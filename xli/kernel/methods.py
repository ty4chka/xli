#!/usr/bin/env python3
"""
XLI Kernel methods — the RPC surface frontends drive.

This is the contract a Go (or any other) client codes against. Everything the
Python CLI can do is reachable here over JSON-RPC, so a foreign frontend is a
presentation layer, not a reimplementation.

Method map
----------
    hello                      protocol handshake
    agent.run      {task}      run a task to completion; streams agent.* events
    agent.tools                the tool catalogue as JSON schema
    tools.list / tools.run     inspect and invoke tools directly
    config.list / config.get / config.set
    kernel.status / kernel.build / kernel.clean / kernel.preflight
    session.list / session.load
    doctor                     environment report

Notifications the server emits while `agent.run` is in flight:

    agent.start, step, assistant, tool_call, tool_result, repair, warning,
    error, agent.end
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xli.kernel.protocol import PROTOCOL_VERSION
from xli.kernel.server import KernelServer


def build_kernel(
    *,
    project_root: Path | None = None,
    provider: Any = None,
    policy: Any = None,
) -> KernelServer:
    """Assemble a kernel with the full method set.

    `provider` and `policy` are injectable so tests (and an embedder that
    already has them) need neither an API key nor a writable config on disk.
    """
    from xli.manager.config import Config
    from xli.permissions.policy import Mode, Policy
    from xli.tools.registry import default_registry

    server = KernelServer()
    root = Path(project_root) if project_root else Path.cwd()
    config = Config.load(project_root=root)
    if policy is None:
        policy = Policy(mode=Mode.parse(config.permission_mode()), root=root)
    registry = default_registry(policy=policy)

    state: dict[str, Any] = {"provider": provider, "config": config}

    # ------------------------------------------------------------------ hello
    @server.method("hello", required=("protocol_version",))
    def hello(protocol_version: int, client: str = "unknown", capabilities: list[str] = None):
        compatible = int(protocol_version) == PROTOCOL_VERSION
        server.client = {
            "protocol_version": int(protocol_version),
            "client": client,
            "capabilities": capabilities or [],
        }
        return {
            "compatible": compatible,
            "protocol_version": PROTOCOL_VERSION,
            "implementation": "xli-kernel",
            "version": _version(),
            "methods": len(server.list_methods()),
        }

    # ------------------------------------------------------------------ agent
    @server.method("agent.run", required=("task",), doc="Run a task to completion.")
    async def agent_run(task: str, max_steps: int | None = None, mode: str | None = None):
        from xli.agent import Agent

        active_policy = policy
        if mode:
            active_policy = Policy(mode=Mode.parse(mode), root=root)
            registry.policy = active_policy

        provider = _resolve_provider(state)

        async def relay(kind: str, payload: dict[str, Any]) -> None:
            await server.notify(f"agent.{kind}", payload)

        agent = Agent(
            provider,
            registry=registry,
            policy=active_policy,
            max_steps=int(max_steps or config.get("agent.max_steps")),
            on_event=lambda kind, payload: _schedule(relay(kind, payload)),
        )
        result = await agent.run(task)
        return result.to_dict()

    @server.method("agent.tools", doc="Tool catalogue as JSON schema.")
    def agent_tools():
        return {"tools": registry.schema(), "prompt": registry.prompt_block()}

    # ------------------------------------------------------------------ tools
    @server.method("tools.list", doc="Every registered tool.")
    def tools_list():
        return {"tools": registry.schema(), "stats": registry.stats()}

    @server.method("tools.run", required=("name",), doc="Invoke one tool.")
    async def tools_run(name: str, args: dict[str, Any] | None = None):
        result = await registry.execute(name, args or {})
        return result.to_dict()

    # ----------------------------------------------------------------- config
    @server.method("config.list", doc="All settings.")
    def config_list():
        return {"config": dict(state["config"].items())}

    @server.method("config.get", required=("key",), doc="Read one setting.")
    def config_get(key: str):
        return {"key": key, "value": state["config"].get(key)}

    @server.method("config.set", required=("key", "value"), doc="Write one setting.")
    def config_set(key: str, value: Any):
        coerced = state["config"].set(key, value)
        path = state["config"].save()
        return {"key": key, "value": coerced, "path": str(path)}

    # ----------------------------------------------------------------- kernel
    @server.method("kernel.preflight", doc="Toolchain readiness.")
    def kernel_preflight():
        from xli.manager import kernel_build

        return kernel_build.preflight().to_dict()

    @server.method("kernel.status", doc="What is compiled.")
    def kernel_status():
        from xli.manager import kernel_build

        return kernel_build.status()

    @server.method("kernel.build", doc="Compile xli.core with Cython.")
    def kernel_build(targets: list[str] | None = None, force: bool = False):
        from xli.manager import kernel_build as kb

        return kb.build(targets, force=force).to_dict()

    @server.method("kernel.clean", doc="Remove build artefacts.")
    def kernel_clean():
        from xli.manager import kernel_build

        return kernel_build.clean()

    # ---------------------------------------------------------------- session
    @server.method("session.list", doc="Stored sessions, newest first.")
    def session_list():
        from xli.session import Session

        return {"sessions": Session.list_sessions(root=root)}

    @server.method("session.load", required=("id",), doc="Replay one session.")
    def session_load(id: str):
        from xli.session import Session

        session = Session.latest(root=root) if id == "--latest" else Session.load(id, root=root)
        if session is None:
            return {"found": False}
        return {
            "found": True,
            "id": session.session_id,
            "events": [e.to_dict() for e in session.events],
            "transcript": session.transcript(),
        }

    # ----------------------------------------------------------------- doctor
    @server.method("doctor", doc="Environment report.")
    def doctor():
        from xli.manager import kernel_build

        checks: list[dict[str, Any]] = [{"name": "python", "ok": True, "detail": _python()}]
        for module in ("httpx", "rich", "Cython", "textual", "pynvim"):
            try:
                __import__(module)
                checks.append({"name": module, "ok": True, "detail": "installed"})
            except ImportError:
                checks.append({"name": module, "ok": False, "detail": "not installed"})
        for check in kernel_build.preflight().checks:
            checks.append(
                {"name": f"kernel.{check.name}", "ok": check.ok, "detail": check.detail}
            )
        return {"checks": checks, "ok": all(c["ok"] for c in checks)}

    return server


# --------------------------------------------------------------------- helpers
def _resolve_provider(state: dict[str, Any]) -> Any:
    """Use the injected provider, or build the configured one on first need."""
    if state["provider"] is None:
        from xli.providers.base import get_provider

        state["provider"] = get_provider()
    return state["provider"]


def _schedule(coro) -> None:
    """Fire a notification coroutine from a sync callback."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    loop.create_task(coro)


def _version() -> str:
    try:
        from xli.cli import VERSION

        return VERSION
    except ImportError:  # pragma: no cover - cli always present in practice
        return "unknown"


def _python() -> str:
    import sys

    return sys.version.split()[0]
