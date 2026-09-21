#!/usr/bin/env python3
"""
XLI TUI — full-screen interface (curses).

The adapter is deliberately dumb. `xli.tui.widgets` decides what belongs on
which line; this file only:

  * owns the terminal (raw mode, no echo, restore on exit)
  * maps bytes from the keyboard to actions
  * paints the rows it is handed

Slash commands and tool approvals are handled here because they are pure input
concerns — neither needs to know anything about curses.

The agent runs on the same event loop as the UI, so a long tool call cannot
freeze input: each turn yields back to the screen between awaits.
"""

from __future__ import annotations

import asyncio
import curses
import sys
from dataclasses import dataclass, field
from typing import Any

from xli.tui.widgets import (
    Row,
    approval_rows,
    header_rows,
    help_rows,
    input_row,
    make_layout,
    scroll_limit,
    status_row,
    transcript_row,
    visible_window,
)

KEY_UP = 259
KEY_DOWN = 258


@dataclass
class TuiState:
    rows: list[Row] = field(default_factory=list)
    scroll: int = 0
    buffer: str = ""
    history: list[str] = field(default_factory=list)
    history_index: int = -1
    busy: bool = False
    show_help: bool = False
    counters: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] | None = None
    approval_future: asyncio.Future | None = None
    allow_all: bool = False
    model: str = ""
    provider: str = ""
    mode: str = "confirm"
    session_id: str = ""
    kernel: str = ""


STYLE_ATTRS = ("normal", "dim", "bold", "accent", "good", "warn", "bad")


def _init_colors() -> dict[str, int]:
    """Map style names to curses attributes; mono terminals degrade cleanly."""
    if not curses.has_colors():
        return dict.fromkeys(STYLE_ATTRS, curses.A_NORMAL)

    curses.start_color()
    curses.use_default_colors()
    pairs = {
        "dim": (245, -1),
        "bold": (255, -1),
        "accent": (39, -1),
        "good": (42, -1),
        "warn": (214, -1),
        "bad": (203, -1),
    }
    mapping = {"normal": curses.A_NORMAL}
    for index, (name, (fg, bg)) in enumerate(pairs.items(), start=1):
        try:
            curses.init_pair(index, fg, bg)
            mapping[name] = curses.color_pair(index)
        except curses.error:
            mapping[name] = curses.A_NORMAL
    mapping["bold"] |= curses.A_BOLD
    return mapping


class Tui:
    """Curses front end for the agent."""

    def __init__(self, config, *, registry=None, policy=None, provider=None):
        self.config = config
        self.registry = registry
        self.policy = policy
        self.provider = provider
        self.state = TuiState(
            model=str(config.get("provider.model")),
            provider=str(config.get("provider")),
            mode=config.permission_mode(),
            kernel="cython" if config.kernel_enabled() else "python",
        )
        self.screen = None
        self.attrs: dict[str, int] = {}
        self._agent_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ paint
    def draw(self) -> None:
        if self.screen is None:
            return
        height, width = self.screen.getmaxyx()
        layout = make_layout(width, height)
        if not layout.is_usable():
            self._draw_too_small(width, height)
            return

        self.screen.erase()
        self._paint_rows(0, header_rows(
            width,
            model=self.state.model,
            provider=self.state.provider,
            mode=self.state.mode,
            session=self.state.session_id,
            kernel=self.state.kernel,
        ))

        if self.state.show_help:
            body = help_rows(width)
        else:
            body = self.state.rows

        window, _ = visible_window(body, layout.body_height, self.state.scroll)
        self._paint_rows(layout.body_top, window)

        if self.state.approval is not None:
            modal = approval_rows(
                self.state.approval["tool"],
                self.state.approval["args"],
                self.state.approval["reason"],
                width,
            )
            top = max(layout.body_top, layout.body_bottom - len(modal))
            self._paint_rows(top, modal)

        self._paint_rows(
            layout.input_top, [input_row(width, " ❯ ", self.state.buffer)]
        )
        self._paint_rows(
            layout.status_top,
            [
                status_row(
                    width,
                    busy=self.state.busy,
                    counters=self.state.counters,
                    hint=(
                        "y/n approve" if self.state.approval else ""
                    ),
                )
            ],
        )
        self.screen.refresh()

    def _draw_too_small(self, width: int, height: int) -> None:
        self.screen.erase()
        message = f"terminal too small ({width}x{height}) — need at least 20x8"
        self.screen.addnstr(0, 0, message, max(0, width - 1))
        self.screen.refresh()

    def _paint_rows(self, top: int, rows: list[Row]) -> None:
        height, width = self.screen.getmaxyx()
        for offset, row in enumerate(rows):
            line = top + offset
            if line >= height:
                break
            column = 0
            for text, style in row:
                if column >= width - 1:
                    break
                attr = self.attrs.get(style, curses.A_NORMAL)
                try:
                    self.screen.addnstr(line, column, text, width - column - 1, attr)
                except curses.error:
                    pass  # writing the bottom-right cell always raises; ignore it
                column += len(text)

    # ------------------------------------------------------------------ input
    def _handle_key(self, key: int) -> bool:
        """Return False to quit."""
        state = self.state

        if state.approval is not None:
            return self._handle_approval_key(key)

        if key in (3, 4):  # ^C / ^D
            return False
        if key == 12:  # ^L
            self.draw()
            return True
        if key == 10 or key == 13:  # Enter
            self._submit()
            return True
        if key == curses.KEY_UP:
            self._history(-1)
            return True
        if key == curses.KEY_DOWN:
            self._history(1)
            return True
        if key in (curses.KEY_PPAGE,):
            state.scroll = min(state.scroll + 10, scroll_limit(len(state.rows), self._body_height()))
            return True
        if key in (curses.KEY_NPAGE,):
            state.scroll = max(0, state.scroll - 10)
            return True
        if key == curses.KEY_HOME:
            state.scroll = scroll_limit(len(state.rows), self._body_height())
            return True
        if key == curses.KEY_END:
            state.scroll = 0
            return True
        if key in (curses.KEY_BACKSPACE, 127, 8):
            state.buffer = state.buffer[:-1]
            return True
        if key == 21:  # ^U clears the line
            state.buffer = ""
            return True
        if 32 <= key < 127 or key > 127:
            try:
                state.buffer += chr(key)
            except (ValueError, OverflowError):
                pass
            return True
        return True

    def _body_height(self) -> int:
        height, _ = self.screen.getmaxyx()
        return make_layout(0, height).body_height

    def _history(self, direction: int) -> None:
        state = self.state
        if not state.history:
            return
        if direction < 0:
            if state.history_index == -1:
                state.history_index = len(state.history) - 1
            elif state.history_index > 0:
                state.history_index -= 1
        else:
            if state.history_index == -1:
                return
            state.history_index += 1
            if state.history_index >= len(state.history):
                state.history_index = -1
                state.buffer = ""
                return
        state.buffer = state.history[state.history_index]

    def _submit(self) -> None:
        text = self.state.buffer.strip()
        self.state.buffer = ""
        self.state.history_index = -1
        if not text:
            return
        self.state.history.append(text)

        if text.startswith("/"):
            self._slash(text)
            return

        for row in transcript_row("user", {"text": text}, self._width()):
            self.state.rows.append(row)
        self._run_task(text)

    def _slash(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        width = self._width()

        def note(message: str) -> None:
            self.state.rows.extend(transcript_row("note", {"text": message}, width))

        if command == "help":
            self.state.show_help = not self.state.show_help
        elif command == "tools":
            names = self.registry.names() if self.registry else []
            note("tools: " + ", ".join(names))
        elif command == "mode" and argument in ("auto", "confirm", "readonly"):
            self.state.mode = argument
            self.config.set("permissions.mode", argument)
            if self.policy is not None:
                from xli.permissions.policy import Mode

                self.policy.mode = Mode.parse(argument)
            note(f"permission mode: {argument}")
        elif command == "model" and argument:
            self.state.model = argument
            self.config.set("provider.model", argument)
            note(f"model: {argument}")
        elif command == "session":
            note(f"session: {self.state.session_id or '(none)'}")
        elif command == "clear":
            self.state.rows = []
            self.state.scroll = 0
        elif command in ("quit", "exit"):
            raise SystemExit(0)
        else:
            note(f"unknown command: /{command} — try /help")

        self.draw()

    def _handle_approval_key(self, key: int) -> bool:
        future = self.state.approval_future
        if key in (ord("y"), ord("Y")):
            self._resolve_approval(True)
        elif key in (ord("n"), ord("N"), 27):
            self._resolve_approval(False)
        elif key in (ord("a"), ord("A")):
            self.state.allow_all = True
            self._resolve_approval(True)
        elif key in (3, 4):
            self._resolve_approval(False)
            return False
        return True

    def _resolve_approval(self, approved: bool) -> None:
        future = self.state.approval_future
        self.state.approval_future = None
        tool = (self.state.approval or {}).get("tool", "")
        self.state.approval = None
        self.state.rows.extend(
            transcript_row(
                "note",
                {"text": f"{tool}: {'approved' if approved else 'refused'}"},
                self._width(),
            )
        )
        if future is not None and not future.done():
            future.set_result(approved)
        self.draw()

    def _width(self) -> int:
        if self.screen is None:
            return 80
        _, width = self.screen.getmaxyx()
        return width

    # ------------------------------------------------------------------ agent
    def append_event(self, kind: str, payload: dict[str, Any]) -> None:
        width = self._width()
        self.state.rows.extend(transcript_row(kind, payload, width))
        if kind == "tool_call":
            self.state.counters["tools"] = self.state.counters.get("tools", 0) + 1
        if kind == "tool_result" and not payload.get("ok"):
            self.state.counters["errors"] = self.state.counters.get("errors", 0) + 1
        if kind == "step":
            self.state.counters["steps"] = payload.get("index", 0)
        if self.state.scroll == 0:
            self.draw()

    def request_approval(self, tool: str, args: dict[str, Any], reason: str) -> asyncio.Future:
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self.state.approval = {"tool": tool, "args": args, "reason": reason}
        self.state.approval_future = future
        self.draw()
        return future

    def _run_task(self, task: str) -> None:
        if self._agent_task is not None and not self._agent_task.done():
            self.append_event("warning", {"message": "already working — wait for it to finish"})
            return

        async def runner() -> None:
            from xli.agent import Agent
            from xli.session import Session
            from xli.tools.registry import default_registry

            registry = self.registry or default_registry(policy=self.policy)
            session = Session()
            self.state.session_id = session.session_id
            self.state.busy = True
            self.draw()

            if self.policy is not None and not self.state.allow_all:
                registry.confirm_handler = self._confirm_sync

            agent = Agent(
                self.provider,
                registry=registry,
                policy=self.policy,
                session=session,
                max_steps=int(self.config.get("agent.max_steps")),
                on_event=self.append_event,
            )
            try:
                result = await agent.run(task)
                self.append_event(
                    "note", {"text": f"[{result.stopped_reason}] {result.summary}"}
                )
            except Exception as exc:  # noqa: BLE001 - keep the UI alive
                self.append_event("error", {"message": f"{type(exc).__name__}: {exc}"})
            finally:
                self.state.busy = False
                self.draw()

        self._agent_task = asyncio.create_task(runner())

    def _confirm_sync(self, tool: str, args: dict[str, Any], reason: str) -> bool:
        """Bridge the synchronous policy callback onto the async approval UI."""
        if self.state.allow_all:
            return True
        future = self.request_approval(tool, args, reason)
        loop = asyncio.get_event_loop()
        while not future.done():
            self._pump_input()
            loop.run_until_complete(asyncio.sleep(0.02))
        return bool(future.result())

    def _pump_input(self) -> None:
        self.screen.nodelay(True)
        try:
            while True:
                key = self.screen.getch()
                if key == -1:
                    break
                if not self._handle_key(key):
                    raise SystemExit(0)
        finally:
            self.screen.nodelay(False)

    # -------------------------------------------------------------------- run
    async def run_async(self, initial_task: str = "") -> int:
        self.screen.nodelay(False)
        self.screen.timeout(50)  # wake regularly so agent events repaint promptly

        if initial_task:
            self.state.buffer = initial_task
            self._submit()

        while True:
            key = self.screen.getch()
            if key == -1:
                self.draw()
                continue
            if not self._handle_key(key):
                break
            self.draw()
        return 0


def _curses_main(stdscr, app: Tui, initial_task: str) -> int:
    app.screen = stdscr
    app.attrs = _init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)
    app.draw()
    return asyncio.run(app.run_async(initial_task))


def run_tui(config, *, initial_task: str = "", registry=None, policy=None, provider=None) -> int:
    """Entry point used by `xli tui`. Returns a process exit code."""
    if not sys.stdout.isatty():
        print("the TUI needs a terminal — use `xli run` or `xli repl` instead", file=sys.stderr)
        return 2

    app = Tui(config, registry=registry, policy=policy, provider=provider)
    try:
        return curses.wrapper(_curses_main, app, initial_task)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)
