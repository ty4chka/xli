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

from xli import VERSION  # re-exported: `xli --version` reads it

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3



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

    def blue(self, text: str) -> str:
        return self._wrap("34", text)

    def magenta(self, text: str) -> str:
        return self._wrap("35", text)

    def reverse(self, text: str) -> str:
        return self._wrap("7", text)

    def underline(self, text: str) -> str:
        return self._wrap("4", text)

    def strike(self, text: str) -> str:
        return self._wrap("9", text)


STYLE = Style()


# Markdown span styles, mapped to the same vocabulary the TUI's curses palette
# uses, so one renderer drives both front ends.
_ANSI_FOR_STYLE = {
    "normal": None,
    "dim": "2",
    "bold": "1",
    "accent": "36",
    "good": "32",
    "warn": "33",
    "bad": "31",
    "heading": "1;94",
    "code": "7",
    "quote": "3;90",
    "link": "4;36",
    "italic": "3",
    "strike": "9",
}


def render_markdown_ansi(markdown: str, width: int | None = None) -> str:
    """Markdown as ANSI-coloured text for the CLI.

    Uses the same parser as the TUI, so the two never drift apart in what they
    consider a heading or a list. Falls back to plain text when stdout is not a
    terminal or NO_COLOR is set.
    """
    from xli.ui.markdown import render_rows

    if width is None:
        width = _terminal_width()

    rows = render_rows(markdown, width)
    if not STYLE.enabled:
        return "\n".join("".join(t for t, _ in row).rstrip() for row in rows)

    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        for text, style in row:
            code = _ANSI_FOR_STYLE.get(style)
            parts.append(f"\033[{code}m{text}\033[0m" if code else text)
        out.append("".join(parts).rstrip())
    return "\n".join(out)


def _terminal_width(default: int = 80) -> int:
    """Usable width, from COLUMNS or the tty, never wider than the terminal."""
    raw = os.environ.get("COLUMNS")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        return max(20, os.get_terminal_size(sys.stdout.fileno()).columns)
    except (OSError, ValueError, AttributeError):
        return default


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


def _work_mode(args: argparse.Namespace):
    """Resolve --plan/--build into a plan_build.Mode, or None.

    Returns None when neither flag is given, so the caller's normal permission
    posture is untouched.
    """
    requested = getattr(args, "work_mode", None)
    if not requested:
        return None
    from xli.core.plan_build import Mode as WorkMode

    return WorkMode.PLAN if requested == "plan" else WorkMode.BUILD


def _build_registry(config, policy, args: argparse.Namespace):
    from xli.tools.registry import default_registry

    registry = default_registry(policy=policy)
    for name in config.get("tools.disabled") or []:
        registry.set_enabled(name, False)
    return registry


def _build_provider(config):
    """Resolve the configured provider, with a clear message when the key is absent.

    The config is passed through deliberately: get_provider() used to read its
    own singleton, so `--provider` on this command was silently ignored and the
    default provider was built instead.
    """
    from xli.providers.base import get_provider

    return get_provider(config)


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
        mode=_work_mode(args),
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
            # Rendered through the same markdown parser the TUI uses, so a
            # heading or a code fence looks like one in both front ends.
            print(STYLE.cyan("xli "))
            print(render_markdown_ansi(text))
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


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Create, inspect and roll back file snapshots.

    xli.core.time_machine already implemented this and nothing used it. An agent
    that edits files with no way back is a poor default, so the capability is
    exposed here rather than left dead. It is deliberately not automatic: taking
    a snapshot on every edit would spend the user's disk without them asking.
    """
    from xli.core.time_machine import get_time_machine

    machine = get_time_machine()
    action = args.action

    # One positional list, read according to the action, so that the natural
    # spellings all work: `snapshot create a.txt b.txt`,
    # `snapshot rollback <id>`, `snapshot diff <id> a.txt`. A single
    # `path nargs="*"` argument cannot tell an id from a filename, and an
    # --id-only design made the obvious invocation fail.
    targets = list(args.targets or [])
    snapshot_id = args.id
    paths: list[str] = []
    if action == "create":
        paths = targets
    elif action in {"rollback", "delete"}:
        if snapshot_id is None and targets:
            snapshot_id = targets[0]
    elif action == "diff":
        if snapshot_id is None and targets:
            snapshot_id = targets[0]
            targets = targets[1:]
        paths = targets

    if action == "list":
        snapshots = machine.list_snapshots()
        if args.json:
            _emit(snapshots, True)
            return EXIT_OK
        if not snapshots:
            print(STYLE.dim("  no snapshots"))
            return EXIT_OK
        for snap in snapshots:
            print(
                f"  {STYLE.bold(snap['id']):<40} "
                f"{STYLE.dim(snap.get('created', '')[:19])}  "
                f"{snap.get('files', 0)} file(s)  {snap.get('label', '')}"
            )
        return EXIT_OK

    if action == "create":
        if not paths:
            print("usage: xli snapshot create <path> [path ...]", file=sys.stderr)
            return EXIT_USAGE
        missing = [pth for pth in paths if not Path(pth).exists()]
        if missing:
            print(f"not found: {', '.join(missing)}", file=sys.stderr)
            return EXIT_USAGE
        new_id = machine.snapshot(paths, args.label)
        if args.json:
            _emit({"id": new_id, "files": len(paths)}, True)
        else:
            print(f"  {STYLE.green('snapshot')} {STYLE.bold(new_id)}")
        return EXIT_OK

    if action == "rollback":
        if not snapshot_id:
            print("usage: xli snapshot rollback <id>", file=sys.stderr)
            return EXIT_USAGE
        if not machine.rollback(snapshot_id):
            print(f"no such snapshot: {snapshot_id}", file=sys.stderr)
            return EXIT_FAILED
        if args.json:
            _emit({"rolled_back": snapshot_id}, True)
        else:
            print(f"  {STYLE.green('rolled back')} {snapshot_id}")
        return EXIT_OK

    if action == "diff":
        if not snapshot_id or not paths:
            print("usage: xli snapshot diff <id> <path>", file=sys.stderr)
            return EXIT_USAGE
        text = machine.diff_snapshot(snapshot_id, paths[0])
        if args.json:
            _emit({"id": snapshot_id, "diff": text}, True)
        else:
            print(text)
        return EXIT_OK

    if action == "delete":
        if not snapshot_id:
            print("usage: xli snapshot delete <id>", file=sys.stderr)
            return EXIT_USAGE
        if not machine.delete_snapshot(snapshot_id):
            print(f"no such snapshot: {snapshot_id}", file=sys.stderr)
            return EXIT_FAILED
        if args.json:
            _emit({"deleted": snapshot_id}, True)
        else:
            print(f"  {STYLE.green('deleted')} {snapshot_id}")
        return EXIT_OK

    print(f"unknown action: {action}", file=sys.stderr)
    return EXIT_USAGE


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


def cmd_recommend(args: argparse.Namespace) -> int:
    """Suggest the MCP servers and skills that fit a task."""
    from xli.mcp.recommender import get_recommender

    task = " ".join(args.task).strip()
    if not task:
        print("usage: xli recommend <task description>", file=sys.stderr)
        return EXIT_USAGE

    recommender = get_recommender()

    if args.context:
        block = recommender.build_full_context(task, args.agent)
        if args.json:
            _emit({"context": block}, True)
        else:
            print(block)
        return EXIT_OK

    rec = recommender.recommend_for_task(task)
    if args.json:
        _emit(rec, True)
        return EXIT_OK

    servers = rec.get("mcp_servers", [])
    scores = rec.get("mcp_scores", {})
    skills = rec.get("skills", [])
    print(STYLE.bold("MCP servers"))
    if servers:
        for name in servers:
            print(f"  {STYLE.green('+')} {name:<20} {STYLE.dim(f'relevance {scores.get(name, 0):.2f}')}")
    else:
        print(STYLE.dim("  none above the relevance threshold"))
    print()
    print(STYLE.bold("Skills"))
    if skills:
        for name in skills:
            print(f"  {STYLE.green('+')} {name}")
    else:
        print(STYLE.dim("  no matching skills"))
    return EXIT_OK


def cmd_scout(args: argparse.Namespace) -> int:
    """Scan the project and report (or write) AGENTS.md."""
    from xli.core.context_scout import ContextScout

    root = Path(args.path).resolve() if args.path else Path.cwd()
    if not root.is_dir():
        print(STYLE.red(f"no such directory: {root}"), file=sys.stderr)
        return EXIT_USAGE

    scout = ContextScout(str(root))

    if args.save:
        written = scout.save_agents_md()
        if args.json:
            _emit({"saved": True, "path": str(written)}, True)
        else:
            print(STYLE.green(f"wrote {written}"))
        return EXIT_OK

    ctx = scout.scan()
    if args.json:
        from dataclasses import asdict

        _emit(
            {
                "name": ctx.name,
                "language": ctx.language,
                "framework": ctx.framework,
                "key_files": ctx.key_files,
                "dependencies": ctx.dependencies,
                "conventions": ctx.conventions,
                "patterns": [asdict(pat) for pat in ctx.patterns],
            },
            True,
        )
        return EXIT_OK

    print(STYLE.bold(ctx.name))
    print(f"  language    {ctx.language}")
    print(f"  framework   {ctx.framework}")
    print(f"  key files   {len(ctx.key_files)}")
    print(f"  deps        {len(ctx.dependencies)}{': ' + ', '.join(ctx.dependencies[:8]) if ctx.dependencies else ''}")
    print(f"  patterns    {len(ctx.patterns)}{': ' + ', '.join(p.name for p in ctx.patterns[:6]) if ctx.patterns else ''}")
    if ctx.conventions:
        print(f"  conventions {', '.join(ctx.conventions[:5])}")
    print(STYLE.dim("  use --save to write AGENTS.md"))
    return EXIT_OK


def cmd_inbox(args: argparse.Namespace) -> int:
    """Inter-agent messaging (TeamInbox)."""
    from xli.core.inbox import TeamInbox

    box = TeamInbox(project=args.project, team=args.team)

    if args.action == "send":
        if not (args.sender and args.recipient and args.text):
            print("usage: xli inbox send --sender A --recipient B --text '...'", file=sys.stderr)
            return EXIT_USAGE
        msg = asyncio.run(box.send(args.sender, args.recipient, args.text))
        if args.json:
            _emit({"sent": msg.to_dict()}, True)
        else:
            print(STYLE.green(f"→ {args.recipient}"), STYLE.dim(msg.id))
        return EXIT_OK

    if args.action == "read":
        if not args.recipient:
            print("usage: xli inbox read --recipient A", file=sys.stderr)
            return EXIT_USAGE
        msgs = box.read_messages(args.recipient, limit=args.limit)
        if args.json:
            _emit({"agent": args.recipient, "messages": [m.to_dict() for m in msgs]}, True)
        elif not msgs:
            print(STYLE.dim(f"no messages for {args.recipient}"))
        else:
            for m in msgs:
                print(f"  {STYLE.dim(m.timestamp[:19])} {STYLE.bold(m.from_agent)}: {m.text}")
        return EXIT_OK

    if args.action == "broadcast":
        if not (args.sender and args.text):
            print("usage: xli inbox broadcast --sender A --text '...'", file=sys.stderr)
            return EXIT_USAGE
        sent = asyncio.run(box.broadcast(args.sender, args.text)) or []
        if args.json:
            _emit({"sent": [m.to_dict() for m in sent]}, True)
        elif not sent:
            print(STYLE.dim("no recipients — nobody has an inbox yet"))
        else:
            print(STYLE.green(f"→ {len(sent)} agent(s): ") + ", ".join(m.to_agent for m in sent))
        return EXIT_OK

    # status
    agents = sorted(f.stem for f in box.base_dir.glob("*.jsonl")) if box.base_dir.is_dir() else []
    if args.json:
        _emit(
            {
                "project": box.project,
                "team": box.team,
                "dir": str(box.base_dir),
                "agents": [{"agent": a, "messages": len(box.read_messages(a))} for a in agents],
            },
            True,
        )
    else:
        print(STYLE.dim(f"{box.project}/{box.team} → {box.base_dir}"))
        if not agents:
            print(STYLE.dim("  no agents yet"))
        for a in agents:
            print(f"  {a:<20} {len(box.read_messages(a))} message(s)")
    return EXIT_OK


def cmd_plugins(args: argparse.Namespace) -> int:
    """Inspect and control the internal (XPI) plugin system."""
    from xli.xpi.manager import XPI_DIR, XpiManager
    from xli.xpi.state import XpiState

    manager = XpiManager()

    if args.action == "list":
        plugins = manager.list_plugins()
        if args.json:
            _emit({"dir": str(XPI_DIR), "plugins": plugins}, True)
        else:
            print(STYLE.dim(f"plugin dir: {XPI_DIR}"))
            if not plugins:
                print(STYLE.dim("  no plugins installed"))
            for info in plugins:
                state = (
                    STYLE.green("active")
                    if info["enabled"] and not info["error"]
                    else STYLE.red("error")
                    if info["error"]
                    else STYLE.dim("disabled")
                )
                print(f"  {state:<8} {STYLE.bold(info['name']):<20} v{info['version']}")
                if info["error"]:
                    print(STYLE.red(f"           {info['error']}"))
                elif info["hooks"]:
                    print(STYLE.dim(f"           hooks: {', '.join(info['hooks'])}"))
        return EXIT_OK

    if args.action in ("enable", "disable"):
        if not args.name:
            print(f"usage: xli plugins {args.action} <name>", file=sys.stderr)
            return EXIT_USAGE
        ok = manager.set_enabled(args.name, args.action == "enable")
        print(
            STYLE.green(f"{args.action}d {args.name}")
            if ok
            else STYLE.red(f"no such plugin: {args.name}")
        )
        return EXIT_OK if ok else EXIT_USAGE

    if args.action == "reload":
        if not args.name:
            print("usage: xli plugins reload <name>", file=sys.stderr)
            return EXIT_USAGE
        ok = manager.reload(args.name)
        print(
            STYLE.green(f"reloaded {args.name}") if ok else STYLE.red(f"reload failed: {args.name}")
        )
        return EXIT_OK if ok else EXIT_FAILED

    if args.action == "state":
        _emit(XpiState().all(), args.json)
        return EXIT_OK

    if args.action == "dispatch":
        if not args.name:
            print("usage: xli plugins dispatch <hook>", file=sys.stderr)
            return EXIT_USAGE
        _emit(manager.dispatch(args.name).to_dict(), args.json)
        return EXIT_OK

    print(f"unknown plugins action: {args.action}", file=sys.stderr)
    return EXIT_USAGE


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
def cmd_agents(args: argparse.Namespace) -> int:
    """`xli agents` — list, inspect, create, verify and run sub-agents."""
    from xli.agents import AgentSpec, get_registry, reset_registry

    reset_registry()
    registry = get_registry(project_root=Path(args.project) if args.project else Path.cwd())
    action = args.action

    if action == "list":
        rows = registry.summary()
        if args.json:
            _emit(rows, True)
            return EXIT_OK
        if not rows:
            print("no sub-agents defined")
            return EXIT_OK
        print(STYLE.bold(f"{len(rows)} sub-agent(s)"))
        for row in rows:
            origin = STYLE.dim("built-in") if row["builtin"] else STYLE.green("custom")
            print(f"  {STYLE.bold(row['name']):22} {origin}  {row['mode']}")
            print(STYLE.dim(f"    {row['description']}"))
            print(STYLE.dim(f"    tools: {', '.join(row['tools'])}"))
        return EXIT_OK

    if action == "show":
        spec = registry.get(args.name)
        if spec is None:
            print(STYLE.red(f"no such sub-agent: {args.name}"), file=sys.stderr)
            return EXIT_USAGE
        if args.json:
            _emit(spec.to_dict(), True)
        else:
            print(spec.system_prompt(project=args.project or str(Path.cwd())))
        return EXIT_OK

    if action == "verify":
        from xli.tools.registry import default_registry

        available = default_registry().names(enabled_only=False)
        report = registry.verify(available)
        if args.json:
            _emit(report, True)
            return EXIT_OK if not report else EXIT_FAILED
        if not report:
            print(STYLE.green(f"all {len(registry)} sub-agent(s) valid"))
            return EXIT_OK
        for name, problems in sorted(report.items()):
            print(STYLE.red(f"  {name}"))
            for problem in problems:
                print(STYLE.dim(f"    - {problem}"))
        return EXIT_FAILED

    if action == "create":
        if not args.name:
            print("usage: xli agents create <name> --description ... --role ...", file=sys.stderr)
            return EXIT_USAGE
        spec = AgentSpec(
            name=args.name,
            description=args.description or "",
            role=args.role or "",
            tools=[t.strip() for t in (args.tools or "read,ls,glob,grep").split(",") if t.strip()],
            mode=args.agent_mode or "confirm",
            max_steps=args.max_steps or 12,
        )
        problems = spec.validate()
        if problems:
            for problem in problems:
                print(STYLE.red(f"  {problem}"), file=sys.stderr)
            return EXIT_USAGE
        path = registry.save(spec, project=bool(args.to_project))
        print(STYLE.green(f"created {spec.name}"))
        print(STYLE.dim(f"wrote {path}"))
        return EXIT_OK

    if action == "delete":
        if not args.name:
            print("usage: xli agents delete <name>", file=sys.stderr)
            return EXIT_USAGE
        spec = registry.get(args.name)
        if spec is None:
            print(STYLE.red(f"no such sub-agent: {args.name}"), file=sys.stderr)
            return EXIT_USAGE
        if spec.builtin:
            print(
                STYLE.red(f"{args.name} is built-in and cannot be deleted; "
                          "override it with `xli agents create --project`"),
                file=sys.stderr,
            )
            return EXIT_USAGE
        registry.delete(args.name)
        print(STYLE.green(f"deleted {args.name}"))
        return EXIT_OK

    print(f"unknown agents action: {action}", file=sys.stderr)
    return EXIT_USAGE


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

    # --- recommend
    snapshot = sub.add_parser("snapshot", help="create, inspect and roll back file snapshots")
    snapshot.add_argument("action", choices=["list", "create", "rollback", "diff", "delete"])
    snapshot.add_argument(
        "targets",
        nargs="*",
        help="create: file(s); rollback/delete: <id>; diff: <id> <path>",
    )
    snapshot.add_argument("--id", help="snapshot id (alternative to passing it positionally)")
    snapshot.add_argument("--label", help="label for a new snapshot")
    snapshot.add_argument("--json", action="store_true")
    snapshot.set_defaults(func=cmd_snapshot)

    recommend = sub.add_parser("recommend", help="suggest MCP servers and skills for a task")
    recommend.add_argument("task", nargs="*", help="what you are about to do")
    recommend.add_argument("--agent", default="coder", help="role to tailor skills for")
    recommend.add_argument("--context", action="store_true", help="print the full context block")
    recommend.add_argument("--json", action="store_true")
    recommend.set_defaults(func=cmd_recommend)

    # --- scout
    scout = sub.add_parser("scout", help="scan the project, generate AGENTS.md")
    scout.add_argument("path", nargs="?", help="project root (default: cwd)")
    scout.add_argument("--save", action="store_true", help="write AGENTS.md")
    scout.add_argument("--json", action="store_true")
    scout.set_defaults(func=cmd_scout)

    # --- inbox
    inbox = sub.add_parser("inbox", help="inter-agent messaging")
    inbox.add_argument("action", choices=["status", "send", "read", "broadcast"], nargs="?", default="status")
    inbox.add_argument("--sender", help="sending agent id")
    inbox.add_argument("--recipient", help="receiving agent id")
    inbox.add_argument("--text", help="message body")
    inbox.add_argument("--project", default="default")
    inbox.add_argument("--team", default="default")
    inbox.add_argument("--limit", type=int, default=50)
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(func=cmd_inbox)

    # --- plugins (XPI)
    plugins = sub.add_parser("plugins", help="internal (XPI) plugins")
    plugins.add_argument(
        "action",
        choices=["list", "enable", "disable", "reload", "state", "dispatch"],
        nargs="?",
        default="list",
    )
    plugins.add_argument("name", nargs="?", help="plugin name, or hook for dispatch")
    plugins.add_argument("--json", action="store_true")
    plugins.set_defaults(func=cmd_plugins)

    # --- doctor
    doctor = sub.add_parser("doctor", help="report what is and is not working")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    agents = sub.add_parser("agents", help="sub-agents: list, create, verify")
    agents_sub = agents.add_subparsers(dest="action", required=True)

    # Every action resolves specs relative to a project, so each one takes the
    # same --project. It is declared per action rather than on `agents` itself,
    # because argparse does not propagate a parent's option into the namespace
    # of a subparser that never saw it.
    def _agents_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        child = agents_sub.add_parser(name, help=help_text)
        child.add_argument("--project", help="project directory (default: cwd)")
        return child

    agents_list = _agents_parser("list", "list sub-agents")
    agents_list.add_argument("--json", action="store_true")

    agents_show = _agents_parser("show", "print a sub-agent's prompt")
    agents_show.add_argument("name")
    agents_show.add_argument("--json", action="store_true")

    agents_verify = _agents_parser("verify", "check every spec is usable")
    agents_verify.add_argument("--json", action="store_true")

    agents_create = _agents_parser("create", "define a new sub-agent")
    agents_create.add_argument("name")
    agents_create.add_argument("--description", help="one line: when to delegate to it")
    agents_create.add_argument("--role", help="the instructions it runs with")
    agents_create.add_argument("--tools", help="comma-separated tool names")
    agents_create.add_argument(
        "--agent-mode", choices=["readonly", "confirm"], help="permission mode"
    )
    agents_create.add_argument("--max-steps", type=int, help="step budget")
    agents_create.add_argument(
        "--to-project",
        action="store_true",
        help="write into <project>/.xli/agents instead of ~/.xli/agents",
    )

    agents_delete = _agents_parser("delete", "remove a custom sub-agent")
    agents_delete.add_argument("name")

    agents.set_defaults(func=cmd_agents)

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
    work = parser.add_mutually_exclusive_group()
    work.add_argument(
        "--plan",
        action="store_const",
        const="plan",
        dest="work_mode",
        help="plan mode: read-only analysis, no file writes or shell",
    )
    work.add_argument(
        "--build",
        action="store_const",
        const="build",
        dest="work_mode",
        help="build mode: may modify files and run commands",
    )


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
