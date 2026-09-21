#!/usr/bin/env python3
"""
XLI CLI — the command-line interface.

Subcommands
-----------
    xli run "task"      run one task and exit
    xli                 start the interactive REPL
    xli tui             full-screen interface
    xli serve           run the JSON-RPC kernel (stdio or unix socket)
    xli config          inspect and change settings
    xli kernel          Cython build of xli.core: preflight/build/status/clean
    xli tools           list tools, dump schemas, show call stats
    xli session         list/show/delete conversation history
    xli skills          list skill definitions
    xli mcp             list MCP servers
    xli nvim            install the Neovim plugin
    xli doctor          report what is and is not working

Every command has a `--json` flag where it makes sense, so the CLI is usable as
a building block in scripts, not only by a human.

Exit codes are meaningful: 0 success, 1 the task failed, 2 bad usage, 3 the
environment is broken (missing key, missing toolchain).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

VERSION = "6.0.0"


# --------------------------------------------------------------------- output
def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(payload)


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


class Style:
    """Minimal ANSI helper; degrades to no-ops when stdout is not a terminal."""

    def __init__(self, enabled: bool | None = None):
        self.enabled = _supports_color() if enabled is None else enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)


STYLE = Style()


# --------------------------------------------------------------------- wiring
def _load_config(args: argparse.Namespace):
    from xli.manager.config import Config

    config = Config.load(project_root=Path(args.project) if args.project else None)
    if getattr(args, "provider", None):
        config.set("provider", args.provider)
    if getattr(args, "model", None):
        config.set("provider.model", args.model)
    if getattr(args, "mode", None):
        config.set("permissions.mode", args.mode)
    if getattr(args, "max_steps", None):
        config.set("agent.max_steps", args.max_steps)
    return config


def _build_policy(config, args: argparse.Namespace):
    from xli.permissions.policy import Mode, Policy

    deny = list(config.get("permissions.deny") or [])
    allow = list(config.get("permissions.allow") or [])
    for extra in getattr(args, "deny", None) or []:
        deny.extend(extra.split(","))
    return Policy(
        mode=Mode.parse(config.permission_mode()),
        deny=deny,
        allow=allow,
        root=Path(args.project) if args.project else Path.cwd(),
    )


def _build_registry(config, policy, args: argparse.Namespace):
    from xli.tools.registry import default_registry

    registry = default_registry(policy=policy)
    for name in config.get("tools.disabled") or []:
        registry.set_enabled(name, False)
    return registry


def _build_provider(config):
    """Resolve the configured provider, with a clear message when the key is absent."""
    from xli.providers.base import get_provider

    return get_provider()


# ------------------------------------------------------------------- commands
def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    policy = _build_policy(config, args)
    registry = _build_registry(config, policy, args)

    from xli.agent import Agent
    from xli.session import Session

    session = None
    if not args.no_session:
        session = Session(root=Path(args.project) if args.project else Path.cwd())

    try:
        provider = _build_provider(config)
    except ValueError as exc:
        print(STYLE.red(f"environment: {exc}"), file=sys.stderr)
        print(
            STYLE.dim("  set the API key, or run `xli config set provider <name>`"),
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT

    events: list[dict[str, Any]] = []

    def on_event(kind: str, payload: dict[str, Any]) -> None:
        events.append({"event": kind, **payload})
        if args.json:
            return
        _render_event(kind, payload)

    if policy.mode.value == "confirm" and not args.yes:
        registry.confirm_handler = _terminal_confirm

    agent = Agent(
        provider,
        registry=registry,
        policy=policy,
        session=session,
        max_steps=int(config.get("agent.max_steps")),
        temperature=float(config.get("provider.temperature")),
        max_tokens=int(config.get("provider.max_tokens")),
        on_event=on_event,
    )

    result = asyncio.run(agent.run(" ".join(args.task)))

    if args.json:
        _emit({**result.to_dict(), "events": events, "session": session.session_id if session else None}, True)
    else:
        print()
        colour = STYLE.green if result.ok else STYLE.red
        print(colour(STYLE.bold(f"[{result.stopped_reason}] ") + result.summary))
        if session:
            print(STYLE.dim(f"session {session.session_id}"))

    return EXIT_OK if result.ok else EXIT_FAILED


def _render_event(kind: str, payload: dict[str, Any]) -> None:
    if kind == "assistant":
        text = payload.get("text", "").strip()
        if text:
            print(STYLE.cyan("xli ") + text)
    elif kind == "tool_call":
        args_text = json.dumps(payload.get("args", {}), ensure_ascii=False)
        if len(args_text) > 120:
            args_text = args_text[:117] + "..."
        print(STYLE.dim(f"  -> {payload.get('name')} {args_text}"))
    elif kind == "tool_result":
        mark = STYLE.green("ok") if payload.get("ok") else STYLE.red("FAIL")
        print(STYLE.dim(f"     [{mark}] {payload.get('summary', '')[:120]}"))
    elif kind == "repair":
        print(STYLE.yellow(f"  repaired: {payload.get('detail')}"))
    elif kind == "warning":
        print(STYLE.yellow(f"  warning: {payload.get('message')}"))
    elif kind == "error":
        print(STYLE.red(f"  error: {payload.get('message')}"))
    elif kind == "step":
        print(STYLE.dim(f"-- step {payload.get('index')}/{payload.get('max_steps')}"))


def _terminal_confirm(tool: str, args: dict[str, Any], reason: str) -> bool:
    """Ask on the terminal; EOF or anything but yes means no."""
    print(STYLE.yellow(f"  {tool} needs approval ({reason})"))
    print(STYLE.dim(f"    args: {json.dumps(args, ensure_ascii=False)[:200]}"))
    try:
        answer = input(STYLE.bold("  allow? [y/N] ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def cmd_serve(args: argparse.Namespace) -> int:
    from xli.kernel.daemon import serve_stdio, serve_unix, socket_path
    from xli.kernel.methods import build_kernel

    kernel = build_kernel()

    if args.unix:
        path = Path(args.unix) if args.unix != "-" else socket_path()
        print(STYLE.dim(f"kernel listening on {path}"), file=sys.stderr)
        try:
            asyncio.run(serve_unix(kernel, path))
        except KeyboardInterrupt:
            pass
        return EXIT_OK

    asyncio.run(serve_stdio(kernel))
    return EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    from xli.manager.config import Config, ConfigError, reset_config

    reset_config()
    config = Config.load(project_root=Path(args.project) if args.project else None)

    if args.action == "list":
        if args.json:
            _emit(dict(config.items()), True)
        else:
            for key, value in config.items():
                print(f"{STYLE.bold(key)} = {json.dumps(value, ensure_ascii=False)}")
            if config.unknown_keys:
                print(STYLE.yellow(f"\nunknown keys: {', '.join(config.unknown_keys)}"))
        return EXIT_OK

    if args.action == "get":
        if not args.key:
            print("usage: xli config get <key>", file=sys.stderr)
            return EXIT_USAGE
        value = config.get(args.key)
        _emit(value, args.json)
        return EXIT_OK

    if args.action == "set":
        if not args.key or args.value is None:
            print("usage: xli config set <key> <value>", file=sys.stderr)
            return EXIT_USAGE
        try:
            coerced = config.set(args.key, args.value)
        except ConfigError as exc:
            print(STYLE.red(str(exc)), file=sys.stderr)
            return EXIT_USAGE
        path = config.save(Path(args.output) if args.output else None)
        print(STYLE.green(f"set {args.key} = {json.dumps(coerced, ensure_ascii=False)}"))
        print(STYLE.dim(f"wrote {path}"))
        return EXIT_OK

    if args.action == "path":
        _emit(
            {"user": str(Config.user_path()), "project": str(Config.project_path())}, args.json
        )
        return EXIT_OK

    print(f"unknown config action: {args.action}", file=sys.stderr)
    return EXIT_USAGE


def cmd_kernel(args: argparse.Namespace) -> int:
    from xli.manager import kernel_build

    if args.action == "preflight":
        result = kernel_build.preflight()
        if args.json:
            _emit(result.to_dict(), True)
        else:
            print(STYLE.bold("kernel toolchain"))
            print(result.report())
            print()
            print(
                STYLE.green("ready to build")
                if result.ok
                else STYLE.yellow("not ready — fix the items above, or keep using pure Python")
            )
        return EXIT_OK if result.ok else EXIT_ENVIRONMENT

    if args.action == "status":
        info = kernel_build.status()
        if args.json:
            _emit(info, True)
        else:
            print(
                f"{STYLE.bold('kernel')}: {info['compiled']}/{info['total']} compiled"
                + (STYLE.dim(f"  built {info['built_at']}") if info["built_at"] else "")
            )
            counts = {
                "compiled": STYLE.green,
                "stale": STYLE.yellow,
                "source-only": STYLE.dim,
            }
            for module in info["modules"]:
                colour = counts.get(module["state"], str)
                print(f"  {colour(module['state']):>22}  {module['module']}")
            if not info["preflight_ok"]:
                print(STYLE.yellow("\ntoolchain incomplete — run `xli kernel preflight`"))
        return EXIT_OK

    if args.action == "build":
        targets = args.targets or None
        print(STYLE.dim(f"building {len(targets) if targets else 'all'} module(s)..."))
        report = kernel_build.build(targets, force=args.force, verbose=args.verbose)
        if args.json:
            _emit(report.to_dict(), True)
        else:
            print(report.summary())
            if report.preflight and not report.preflight.ok:
                print()
                print(report.preflight.report())
        return EXIT_OK if report.ok else EXIT_ENVIRONMENT

    if args.action == "clean":
        result = kernel_build.clean()
        _emit(result, args.json)
        return EXIT_OK

    print(f"unknown kernel action: {args.action}", file=sys.stderr)
    return EXIT_USAGE


def cmd_tools(args: argparse.Namespace) -> int:
    config = _load_config(args)
    policy = _build_policy(config, args)
    registry = _build_registry(config, policy, args)

    if args.action == "list":
        if args.json:
            _emit(registry.schema(), True)
        else:
            for name in registry.names():
                spec = registry.get(name).spec
                flag = STYLE.yellow("mutates") if spec.mutates else STYLE.dim("read")
                print(f"  {STYLE.bold(name):<18} {flag}  {spec.description[:70]}")
        return EXIT_OK

    if args.action == "schema":
        _emit(registry.schema(), True)
        return EXIT_OK

    if args.action == "prompt":
        print(registry.prompt_block())
        return EXIT_OK

    if args.action == "enable" or args.action == "disable":
        if not args.name:
            print(f"usage: xli tools {args.action} <name>", file=sys.stderr)
            return EXIT_USAGE
        ok = registry.set_enabled(args.name, args.action == "enable")
        print(
            STYLE.green(f"{args.action}d {args.name}")
            if ok
            else STYLE.red(f"no such tool: {args.name}")
        )
        return EXIT_OK if ok else EXIT_USAGE

    print(f"unknown tools action: {args.action}", file=sys.stderr)
    return EXIT_USAGE


def cmd_session(args: argparse.Namespace) -> int:
    from xli.session import Session

    root = Path(args.project) if args.project else Path.cwd()

    if args.action == "list":
        listing = Session.list_sessions(root=root)
        _emit(listing, args.json)
        return EXIT_OK

    if args.action == "show":
        if not args.id:
            print("usage: xli session show <id|--latest>", file=sys.stderr)
            return EXIT_USAGE
        session = (
            Session.latest(root=root)
            if args.id == "--latest"
            else Session.load(args.id, root=root)
        )
        if session is None:
            print("no sessions found", file=sys.stderr)
            return EXIT_FAILED
        _emit(session.transcript() if not args.json else [e.to_dict() for e in session.events], args.json)
        return EXIT_OK

    if args.action == "delete":
        if not args.id:
            print("usage: xli session delete <id>", file=sys.stderr)
            return EXIT_USAGE
        ok = Session(args.id, root=root).delete()
        print(STYLE.green(f"deleted {args.id}") if ok else STYLE.red("delete failed"))
        return EXIT_OK if ok else EXIT_FAILED

    print(f"unknown session action: {args.action}", file=sys.stderr)
    return EXIT_USAGE


def cmd_skills(args: argparse.Namespace) -> int:
    from xli.core.skills import SkillsManager

    manager = SkillsManager()
    skills = manager.list_skills()
    if args.json:
        _emit(skills, True)
    else:
        for skill in skills:
            name = skill.get("name") if isinstance(skill, dict) else str(skill)
            print(f"  {name}")
        print(STYLE.dim(f"{len(skills)} skill(s)"))
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    from xli.mcp.registry import SERVERS

    if args.json:
        _emit(SERVERS, True)
        return EXIT_OK
    for name, info in sorted(SERVERS.items()):
        state = STYLE.green("on") if info.get("enabled") else STYLE.dim("off")
        print(f"  {state}  {STYLE.bold(name):<18} {info.get('description', '')}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what works. Exit non-zero if something the user needs is broken."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python", True, sys.version.split()[0])

    for module in ("httpx", "rich", "Cython", "textual", "pynvim"):
        try:
            __import__(module)
            add(module, True, "installed")
        except ImportError:
            add(module, False, "not installed")

    from xli.manager import kernel_build

    pre = kernel_build.preflight()
    for check in pre.checks:
        add(f"kernel.{check.name}", check.ok, check.detail)

    try:
        from xli.providers.base import get_provider

        get_provider()
        add("provider", True, "resolved")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        add("provider", False, str(exc))

    from xli.tools.registry import default_registry

    add("tools", True, f"{len(default_registry())} built-in")

    if args.json:
        _emit({"checks": checks, "ok": all(c["ok"] for c in checks)}, True)
    else:
        for check in checks:
            mark = STYLE.green("ok") if check["ok"] else STYLE.yellow("--")
            print(f"  [{mark}] {STYLE.bold(check['name']):<22} {check['detail']}")

    blocking = [c for c in checks if not c["ok"] and c["name"].startswith(("provider", "python"))]
    return EXIT_ENVIRONMENT if blocking else EXIT_OK


def cmd_nvim(args: argparse.Namespace) -> int:
    from xli.nvim.install import install_plugin

    result = install_plugin(target=Path(args.target) if args.target else None)
    _emit(result, args.json)
    return EXIT_OK if result.get("ok") else EXIT_FAILED


def cmd_tui(args: argparse.Namespace) -> int:
    from xli.tui.app import run_tui

    config = _load_config(args)
    return run_tui(config, initial_task=" ".join(args.task) if args.task else "")


def cmd_repl(args: argparse.Namespace) -> int:
    from xli.repl import run_repl

    config = _load_config(args)
    policy = _build_policy(config, args)
    registry = _build_registry(config, policy, args)
    return run_repl(config, registry=registry, policy=policy, args=args)


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xli",
        description="XLI — autonomous coding agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="run `xli <command> --help` for details",
    )
    parser.add_argument("--version", action="version", version=f"xli {VERSION}")

    sub = parser.add_subparsers(dest="command")

    # --- run
    run = sub.add_parser("run", help="run one task and exit")
    run.add_argument("task", nargs="+", help="the task, in plain language")
    run.add_argument("--yes", "-y", action="store_true", help="approve every tool call")
    run.add_argument("--no-session", action="store_true", help="do not persist history")
    run.add_argument("--json", action="store_true", help="emit machine-readable output")
    _add_common(run)
    run.set_defaults(func=cmd_run)

    # --- tui
    tui = sub.add_parser("tui", help="full-screen interface")
    tui.add_argument("task", nargs="*", help="optional starting task")
    _add_common(tui)
    tui.set_defaults(func=cmd_tui)

    # --- repl
    repl = sub.add_parser("repl", help="interactive line REPL")
    repl.add_argument("task", nargs="*", help="optional starting task")
    repl.add_argument("--json", action="store_true")
    _add_common(repl)
    repl.set_defaults(func=cmd_repl)

    # --- serve
    serve = sub.add_parser("serve", help="run the JSON-RPC kernel")
    serve.add_argument("--unix", nargs="?", const="-", help="listen on a unix socket (path optional)")
    serve.set_defaults(func=cmd_serve)

    # --- config
    config = sub.add_parser("config", help="inspect and change settings")
    config.add_argument("action", choices=["list", "get", "set", "path"], nargs="?", default="list")
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.add_argument("--project", help="project directory")
    config.add_argument("--output", help="write the config here instead of the default path")
    config.add_argument("--json", action="store_true")
    config.set_defaults(func=cmd_config)

    # --- kernel
    kernel = sub.add_parser("kernel", help="Cython build of xli.core")
    kernel.add_argument("action", choices=["preflight", "build", "status", "clean"], nargs="?", default="status")
    kernel.add_argument("targets", nargs="*", help="module names to build (default: all)")
    kernel.add_argument("--force", action="store_true", help="rebuild even if up to date")
    kernel.add_argument("--verbose", "-v", action="store_true")
    kernel.add_argument("--json", action="store_true")
    kernel.set_defaults(func=cmd_kernel)

    # --- tools
    tools = sub.add_parser("tools", help="inspect the tool catalogue")
    tools.add_argument("action", choices=["list", "schema", "prompt", "enable", "disable"], nargs="?", default="list")
    tools.add_argument("name", nargs="?")
    tools.add_argument("--json", action="store_true")
    _add_common(tools)
    tools.set_defaults(func=cmd_tools)

    # --- session
    session = sub.add_parser("session", help="conversation history")
    session.add_argument("action", choices=["list", "show", "delete"], nargs="?", default="list")
    session.add_argument("id", nargs="?")
    session.add_argument("--project", help="project directory")
    session.add_argument("--json", action="store_true")
    session.set_defaults(func=cmd_session)

    # --- skills / mcp
    skills = sub.add_parser("skills", help="list skill definitions")
    skills.add_argument("--json", action="store_true")
    skills.set_defaults(func=cmd_skills)

    mcp = sub.add_parser("mcp", help="list MCP servers")
    mcp.add_argument("--json", action="store_true")
    mcp.set_defaults(func=cmd_mcp)

    # --- nvim
    nvim = sub.add_parser("nvim", help="install the Neovim plugin")
    nvim.add_argument("--target", help="install into this directory instead of the nvim config")
    nvim.add_argument("--json", action="store_true")
    nvim.set_defaults(func=cmd_nvim)

    # --- doctor
    doctor = sub.add_parser("doctor", help="report what is and is not working")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", help="LLM provider name")
    parser.add_argument("--model", help="model name")
    parser.add_argument(
        "--mode", choices=["auto", "confirm", "readonly"], help="permission mode"
    )
    parser.add_argument("--max-steps", type=int, help="step budget for one task")
    parser.add_argument("--deny", action="append", help="comma-separated deny patterns")
    parser.add_argument("--project", help="project directory (default: cwd)")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Bare `xli "do the thing"` is a task, not an unknown command.
    if argv and argv[0] not in parser._subparsers._group_actions[0].choices and not argv[0].startswith("-"):
        argv = ["run", *argv]

    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        return cmd_repl(argparse.Namespace(
            task=[], provider=None, model=None, mode=None, max_steps=None,
            deny=None, project=None, json=False,
        ))

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
