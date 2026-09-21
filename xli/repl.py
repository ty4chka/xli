#!/usr/bin/env python3
"""
XLI REPL — the interactive line interface.

This is what `xli` with no arguments runs: a prompt, a transcript, and slash
commands. It is intentionally not the TUI — it works over ssh, inside a pipe,
and in terminals curses cannot drive, which is where most agent sessions
actually happen.

Slash commands mirror the TUI's, so the two can be swapped without relearning
anything.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
from pathlib import Path
from typing import Any

PROMPT = "❯ "

HELP = """\
commands
  /help                     this text
  /tools                    list the tools the agent can call
  /mode auto|confirm|readonly
  /deny PATTERN             add a deny rule (e.g. /deny '/etc/*')
  /model NAME               switch model
  /session                  show the session id
  /clear                    clear the screen
  /quit                     exit

anything else is sent to the agent as a task.
"""


def _print(text: str = "", *, colour: bool = True) -> None:
    if not colour or not sys.stdout.isatty():
        print(text)
        return
    print(text)


class Repl:
    """Line-based front end for the agent."""

    def __init__(self, config, *, registry=None, policy=None, provider=None):
        self.config = config
        self.registry = registry
        self.policy = policy
        self.provider = provider
        self.session = None
        self.allow_all = False
        self.history: list[str] = []

    # ------------------------------------------------------------------ events
    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "assistant":
            text = str(payload.get("text", "")).strip()
            if text:
                _print(f"\033[32mxli\033[0m {text}")
        elif kind == "tool_call":
            _print(f"\033[2m  -> {payload.get('name')} {payload.get('args')}\033[0m")
        elif kind == "tool_result":
            mark = "\033[32mok\033[0m" if payload.get("ok") else "\033[31mFAIL\033[0m"
            _print(f"\033[2m     [{mark}] {str(payload.get('summary', ''))[:140]}\033[0m")
        elif kind == "repair":
            _print(f"\033[33m  repaired: {payload.get('detail')}\033[0m")
        elif kind == "warning":
            _print(f"\033[33m  warning: {payload.get('message')}\033[0m")
        elif kind == "error":
            _print(f"\033[31m  error: {payload.get('message')}\033[0m")

    # ----------------------------------------------------------------- confirm
    def confirm(self, tool: str, args: dict[str, Any], reason: str) -> bool:
        if self.allow_all:
            return True
        _print(f"\033[33m  {tool} needs approval ({reason})\033[0m")
        _print(f"\033[2m    {str(args)[:200]}\033[0m")
        try:
            answer = input("  allow? [y/N/a=always] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _print()
            return False
        if answer == "a":
            self.allow_all = True
            return True
        return answer in ("y", "yes")

    # ---------------------------------------------------------------- commands
    def slash(self, line: str) -> bool:
        """Handle a slash command. Returns False to exit the REPL."""
        try:
            parts = shlex.split(line[1:])
        except ValueError:
            parts = line[1:].split()
        command = parts[0].lower() if parts else ""
        argument = parts[1] if len(parts) > 1 else ""

        if command == "help":
            _print(HELP)
        elif command == "tools":
            for name in self.registry.names():
                spec = self.registry.get(name).spec
                flag = "mutates" if spec.mutates else "read"
                _print(f"  {name:<10} {flag:<8} {spec.description[:70]}")
        elif command == "mode":
            if argument in ("auto", "confirm", "readonly"):
                from xli.permissions.policy import Mode

                self.policy.mode = Mode.parse(argument)
                self.config.set("permissions.mode", argument)
                _print(f"permission mode: {argument}")
            else:
                _print("usage: /mode auto|confirm|readonly")
        elif command == "deny":
            if argument:
                self.policy.deny.append(argument)
                _print(f"deny += {argument}")
            else:
                _print("usage: /deny PATTERN")
        elif command == "model":
            if argument:
                self.config.set("provider.model", argument)
                _print(f"model: {argument}")
            else:
                _print("usage: /model NAME")
        elif command == "session":
            _print(f"session: {self.session.session_id if self.session else '(none)'}")
        elif command == "clear":
            _print("\033[2J\033[H", end="")
        elif command in ("quit", "exit", "q"):
            return False
        else:
            _print(f"unknown command: /{command} — try /help")
        return True

    # -------------------------------------------------------------------- loop
    async def run_task(self, task: str) -> None:
        from xli.agent import Agent
        from xli.session import Session

        if self.session is None:
            self.session = Session(root=Path.cwd())

        if self.policy is not None:
            self.registry.confirm_handler = self.confirm

        agent = Agent(
            self.provider,
            registry=self.registry,
            policy=self.policy,
            session=self.session,
            max_steps=int(self.config.get("agent.max_steps")),
            temperature=float(self.config.get("provider.temperature")),
            max_tokens=int(self.config.get("provider.max_tokens")),
            on_event=self.on_event,
        )

        try:
            result = await agent.run(task)
            colour = "\033[32m" if result.ok else "\033[31m"
            _print(f"\n{colour}[{result.stopped_reason}]\033[0m {result.summary}\n")
        except Exception as exc:  # noqa: BLE001 - one bad turn must not end the session
            _print(f"\033[31m  {type(exc).__name__}: {exc}\033[0m")

    def loop(self, initial_task: str = "") -> int:
        from xli.providers.base import get_provider

        if self.provider is None:
            try:
                self.provider = get_provider()
            except ValueError as exc:
                _print(f"\033[31menvironment: {exc}\033[0m")
                return 3

        _print(
            f"\033[1mXLI\033[0m \033[2m{self.config.get('provider')}/{self.config.get('provider.model')}"
            f" · {self.config.permission_mode()} · /help\033[0m\n"
        )

        while True:
            try:
                line = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                _print()
                return 0

            if not line:
                continue

            self.history.append(line)

            if line.startswith("/"):
                if not self.slash(line):
                    return 0
                continue

            try:
                asyncio.run(self.run_task(line))
            except KeyboardInterrupt:
                _print("\n\033[33m  interrupted\033[0m")


def run_repl(config, *, registry=None, policy=None, args: argparse.Namespace | None = None) -> int:
    # Repl is defined in this same module, so it is already in scope. The local
    # import that used to be here re-imported xli.repl from inside xli.repl and
    # showed up as a self-cycle in the dependency graph.
    repl = Repl(config, registry=registry, policy=policy)
    initial = " ".join(getattr(args, "task", []) or []) if args else ""
    return repl.loop(initial)
